"""14.1: real clients use one Nginx TCP entry and land on different ChatServers."""
import codecs, json, os, re, select, socket, subprocess, tempfile, time, uuid
from pathlib import Path
root=Path(__file__).resolve().parents[1]
config='[client]\nuser=chat_module_test\npassword=Module02_test_only!\nhost=127.0.0.1\nprotocol=tcp\n'
def sql(q):
    return subprocess.run(['mysql','--defaults-file=/dev/stdin','-N','chat_module_test','-e',q],input=config,text=True,capture_output=True,check=True).stdout.strip()
def free_port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
def eventually(check, message):
    end=time.monotonic()+6
    while not check():
        assert time.monotonic()<end,message;time.sleep(.05)
class Peer:
    def __init__(self, port):
        self.s=socket.create_connection(('127.0.0.1',port),timeout=5);self.f=self.s.makefile('rb')
    def request(self, data):
        self.s.sendall((json.dumps(data,ensure_ascii=False)+'\n').encode());return json.loads(self.f.readline())
    def close(self):
        self.s.shutdown(socket.SHUT_RDWR);self.f.close();self.s.close()
class Client:
    def __init__(self, port):
        self.p=subprocess.Popen([str(root/'bin/ChatClient'),'127.0.0.1',str(port)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        self.out='';self.pos=0;self.decoder=codecs.getincrementaldecoder('utf-8')();self.until('请选择:')
    def send(self, data):self.p.stdin.write((str(data)+'\n').encode());self.p.stdin.flush()
    def until(self, token):
        end=time.monotonic()+7
        while token not in self.out[self.pos:]:
            assert time.monotonic()<end,self.out
            if select.select([self.p.stdout],[],[],.1)[0]:
                b=os.read(self.p.stdout.fileno(),8192);assert b,self.out;self.out+=self.decoder.decode(b)
        end=self.out.index(token,self.pos)+len(token);out=self.out[self.pos:end];self.pos=end;return out
    def login(self, uid):
        self.send(1);self.until('用户ID:');self.send(uid);self.until('密码:');self.send('demo123');self.until('登录成功');return self.until('请选择:')
    def chat(self, uid, message):
        self.send(3);self.until('接收者ID:');self.send(uid);self.until('消息内容:');self.send(message);return self.until('请选择:')
    def quit(self):self.send(0);assert self.p.wait(timeout=5)==0


with tempfile.TemporaryDirectory(prefix='chat-nginx-test-') as work:
    redis=None;servers=[];clients=[];ids=[];gid=None;logs=[];nginx_started=False
    portA,portB,entry,redisPort=[free_port() for _ in range(4)]
    token=uuid.uuid4().hex[:8];nodeA='test141-A-'+token;nodeB='test141-B-'+token
    base=dict(os.environ,CHAT_DB_NAME='chat_module_test',CHAT_DB_USER='chat_module_test',CHAT_DB_PASSWORD='Module02_test_only!',CHAT_REDIS_HOST='127.0.0.1',CHAT_REDIS_PORT=str(redisPort),CHAT_REDIS_PASSWORD='',CHAT_REDIS_PREFIX='test141:'+token+':')
    nginx_env=dict(os.environ,CHAT_NGINX_PORT=str(entry),CHAT_NGINX_BACKEND_A_PORT=str(portA),CHAT_NGINX_BACKEND_B_PORT=str(portB),CHAT_NGINX_RUN_DIR=str(Path(work)/'nginx'))
    script=str(root/'deploy/nginx/chat-nginx.sh')
    def control(command,env=None,success=True):
        r=subprocess.run([script,command],env=env or nginx_env,text=True,capture_output=True,timeout=7)
        assert (r.returncode==0)==success,(r.stdout,r.stderr)
        return r
    def start(port,node):
        log=open(Path(work)/(node+'.log'),'w+');logs.append(log)
        p=subprocess.Popen([str(root/'bin/ChatServer'),str(port)],env=dict(base,CHAT_SERVER_ID=node),stdout=log,stderr=log);servers.append(p)
        def ready():
            assert p.poll() is None,Path(log.name).read_text()
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=.1):return True
            except OSError:return False
        eventually(ready,'server startup')
    def register(c):
        c.send(2);c.until('用户名:');c.send('nginx141_'+uuid.uuid4().hex[:10]);c.until('密码:');c.send('demo123')
        out=c.until('请使用该ID登录。');uid=int(re.search(r'用户ID: (\d+)',out).group(1));ids.append(uid);c.until('请选择:');return uid
    try:
        log=open(Path(work)/'redis.log','w+');logs.append(log)
        redis=subprocess.Popen(['redis-server','--bind','127.0.0.1','--port',str(redisPort),'--save','','--appendonly','no','--dir',work],stdout=log,stderr=log)
        eventually(lambda:subprocess.run(['redis-cli','-p',str(redisPort),'ping'],capture_output=True).stdout.strip()==b'PONG','Redis ready')
        start(portA,nodeA);start(portB,nodeB)
        control('test');control('start');nginx_started=True;control('status')
        pid=(Path(work)/'nginx/nginx.pid').read_text();control('start');assert (Path(work)/'nginx/nginx.pid').read_text()==pid
        a=Client(entry);b=Client(entry);clients.extend([a,b]);uidA=register(a);uidB=register(b);a.login(uidA);b.login(uidB)
        assert {sql(f'SELECT server_id FROM User WHERE id={uidA}'),sql(f'SELECT server_id FROM User WHERE id={uidB}')}=={nodeA,nodeB}
        print('PASS one Nginx entry, real C++ menu registration/login, round-robin different backends and idempotent start',flush=True)
        assert 'Redis跨节点转发' in a.chat(uidB,'统一入口跨节点单聊')
        b.until('统一入口跨节点单聊');b.until('请选择:')
        assert 'Redis跨节点转发' in b.chat(uidA,'统一入口回复');a.until('统一入口回复');a.until('请选择:')
        a.send(4);a.until('群名称:');a.send('Nginx统一入口群');a.until('群描述（可留空）:');a.send('TCP stream proxy')
        out=a.until('选择6刷新群列表。');gid=int(re.search(r'群ID: (\d+)',out).group(1));a.until('请选择:')
        b.send(5);b.until('群ID:');b.send(gid);b.until('加入群成功');b.until('请选择:')
        a.send(7);a.until('群ID:');a.send(gid);a.until('消息内容:');a.send('统一入口群聊');out=a.until('请选择:');assert '本节点 0，Redis跨节点 1' in out,out
        b.until('统一入口群聊');b.until('请选择:')
        assert sql(f'SELECT server_id FROM User WHERE id={uidA}')!=sql(f'SELECT server_id FROM User WHERE id={uidB}')
        print('PASS transparent TCP proxy, cross-node single/group chat and stable backend per long connection',flush=True)
        # Config validation must not replace a running configuration.
        conf=Path(work)/'nginx/nginx.conf';saved=conf.read_bytes()
        control('reload',dict(nginx_env,CHAT_NGINX_PORT='invalid'),success=False);assert conf.read_bytes()==saved
        control('test');assert conf.read_bytes()==saved
        control('reload');assert (Path(work)/'nginx/nginx.pid').read_text()==pid
        assert 'Redis跨节点转发' in a.chat(uidB,'配置校验后仍可通信');b.until('配置校验后仍可通信');b.until('请选择:')
        b.quit();eventually(lambda:sql(f'SELECT state FROM User WHERE id={uidB}')=='offline','B disconnect cleanup')
        assert '已存为离线消息' in a.chat(uidB,'代理入口离线单聊')
        a.send(7);a.until('群ID:');a.send(gid);a.until('消息内容:');a.send('代理入口离线群聊');out=a.until('请选择:');assert '离线存储 1' in out,out
        b2=Client(entry);clients.append(b2);b2.login(uidB)
        assert '[离线单聊]' in b2.out and '代理入口离线单聊' in b2.out and '[离线群聊]' in b2.out and '代理入口离线群聊' in b2.out,b2.out
        assert sql(f'SELECT COUNT(*) FROM OfflineMessage WHERE userid={uidB}')=='0'
        b2.quit();a.quit()
        eventually(lambda:sql('SELECT COUNT(*) FROM User WHERE id IN ('+','.join(map(str,ids))+') AND state="online"')=='0','all users offline')
        print('PASS invalid config rejection, live reload preserves existing connections, disconnect cleanup and offline single/group retrieval through entry',flush=True)
        with socket.create_connection(('127.0.0.1',entry),timeout=5) as s:
            f=s.makefile('rb');request=b'{"msgid":15}\n';s.sendall(request[:5]);s.sendall(request[5:]+request)
            assert json.loads(f.readline())['msgid']==16 and json.loads(f.readline())['msgid']==16
        # A forged/stale PID file must not let the script signal another process.
        guard=Path(work)/'guard';guard.mkdir();(guard/'nginx.pid').write_text(str(os.getpid())+'\n')
        control('stop',dict(nginx_env,CHAT_NGINX_RUN_DIR=str(guard)))
        control('start',dict(nginx_env,CHAT_NGINX_RUN_DIR=str(guard)),success=False)
        control('stop');nginx_started=False
        eventually(lambda:subprocess.run([script,'status'],env=nginx_env,capture_output=True).returncode!=0,'Nginx stopped')
        eventually(lambda:(Path(work)/'nginx/access.log').exists() and f'127.0.0.1:{portA}' in (Path(work)/'nginx/access.log').read_text() and f'127.0.0.1:{portB}' in (Path(work)/'nginx/access.log').read_text(),'upstream logs')
        print('PASS split/coalesced JSON through stream, backend access logs, owned-process stop and reused PID guard',flush=True)
    finally:
        for c in clients:
            if c.p.poll() is None:c.p.terminate();c.p.wait(timeout=3)
        if nginx_started:control('stop')
        for p in servers:
            if p.poll() is None:p.terminate();p.wait(timeout=8)
        if redis and redis.poll() is None:redis.terminate();redis.wait(timeout=5)
        if gid:sql(f'DELETE FROM AllGroup WHERE id={gid}')
        if ids:sql('DELETE FROM User WHERE id IN ('+','.join(map(str,ids))+')')
        for log in logs:log.close()
