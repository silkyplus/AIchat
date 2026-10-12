"""14.3: compare connection distribution with isolated real C++ clients."""
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


results=[]
for strategy in ('round_robin','least_conn'):
    with tempfile.TemporaryDirectory(prefix='chat-strategy-') as work:
        ports=set()
        while len(ports)<4:ports.add(free_port())
        a,b,entry,rport=ports
        token=uuid.uuid4().hex[:8];nodeA='test143-A-'+token;nodeB='test143-B-'+token
        env=dict(os.environ,CHAT_DB_NAME='chat_module_test',CHAT_DB_USER='chat_module_test',CHAT_DB_PASSWORD='Module02_test_only!',CHAT_REDIS_HOST='127.0.0.1',CHAT_REDIS_PORT=str(rport),CHAT_REDIS_PASSWORD='',CHAT_REDIS_PREFIX='test143:'+token+':',CHAT_NODE_A_ID=nodeA,CHAT_NODE_B_ID=nodeB,CHAT_NODE_A_PORT=str(a),CHAT_NODE_B_PORT=str(b),CHAT_NODES_RUN_DIR=str(Path(work)/'nodes'),CHAT_NGINX_RUN_DIR=str(Path(work)/'nginx'),CHAT_NGINX_PORT=str(entry),CHAT_NGINX_BACKEND_A_PORT=str(a),CHAT_NGINX_BACKEND_B_PORT=str(b),CHAT_NGINX_STRATEGY=strategy)
        clients=[];ids=[];redis=None;started=False
        def run(script,action,custom=None,ok=True):
            r=subprocess.run([str(root/script),action],env=custom or env,text=True,capture_output=True,timeout=25)
            assert (r.returncode==0)==ok,(r.stdout,r.stderr)
            return r
        def new_client():
            c=Client(entry);clients.append(c)
            c.send(2);c.until('用户名:');c.send('strategy_'+uuid.uuid4().hex[:10]);c.until('密码:');c.send('demo123')
            uid=int(re.search(r'用户ID: (\d+)',c.until('请使用该ID登录。')).group(1));ids.append(uid);c.until('请选择:');c.login(uid)
            owner=sql(f'SELECT server_id FROM User WHERE id={uid}')
            assert owner in (nodeA,nodeB)
            return c,uid,'A' if owner==nodeA else 'B'
        try:
            with open(Path(work)/'redis.log','w') as log:
                redis=subprocess.Popen(['redis-server','--bind','127.0.0.1','--port',str(rport),'--save','','--appendonly','no','--dir',work],stdout=log,stderr=log)
            eventually(lambda:subprocess.run(['redis-cli','-p',str(rport),'ping'],capture_output=True).stdout.strip()==b'PONG','Redis ready')
            started=True;run('deploy/chat-nodes.sh','start');run('deploy/nginx/chat-nginx.sh','start')
            original=[new_client() for _ in range(6)]
            assert [x[2] for x in original].count('A')==3
            for c,uid,owner in original:
                if owner=='B':c.quit()
            access=Path(work)/'nginx/access.log'
            eventually(lambda:access.exists() and len(access.read_text().splitlines())==3,'closed upstream connections accounted')
            added=[new_client() for _ in range(3)]
            assigned=[x[2] for x in added]
            if strategy=='least_conn':assert assigned==['B']*3,assigned
            else:assert set(assigned)=={'A','B'},assigned
            # Both strategies must preserve cross-node application messaging.
            sender=next(x for x in original if x[2]=='A')
            receiver=next(x for x in added if x[2]=='B')
            assert 'Redis跨节点转发' in sender[0].chat(receiver[1],'strategy cross-node check')
            receiver[0].until('strategy cross-node check');receiver[0].until('请选择:')
            conf=Path(work)/'nginx/nginx.conf';before=conf.read_bytes()
            run('deploy/nginx/chat-nginx.sh','reload',dict(env,CHAT_NGINX_STRATEGY='invalid'),ok=False)
            assert conf.read_bytes()==before
            # Reload changes policy for future connections while existing sessions survive.
            switched='least_conn' if strategy=='round_robin' else 'round_robin'
            env['CHAT_NGINX_STRATEGY']=switched
            run('deploy/nginx/chat-nginx.sh','reload')
            assert '# strategy: '+switched in conf.read_text()
            assert 'Redis跨节点转发' in sender[0].chat(receiver[1],'existing sessions survive reload')
            receiver[0].until('existing sessions survive reload');receiver[0].until('请选择:')
            new_client()
            row=dict(strategy=strategy,initial={'A':3,'B':3},after_close={'A':3,'B':0},new_connections={'A':assigned.count('A'),'B':assigned.count('B')},final={'A':3+assigned.count('A'),'B':assigned.count('B')})
            results.append(row);print(json.dumps(row),flush=True)
            print('PASS distribution, cross-node chat, invalid policy rejection, live policy reload',flush=True)
        finally:
            for c in clients:
                if c.p.poll() is None:c.p.terminate();c.p.wait(timeout=3)
            if started:
                run('deploy/nginx/chat-nginx.sh','stop');run('deploy/chat-nodes.sh','stop')
            if redis and redis.poll() is None:redis.terminate();redis.wait(timeout=5)
            if ids:sql('DELETE FROM User WHERE id IN ('+','.join(map(str,ids))+')')
report=root/'.runtime/nginx-strategy-comparison.json';report.parent.mkdir(exist_ok=True)
report.write_text(json.dumps(results,indent=2)+'\n')
print('Report:',report)
