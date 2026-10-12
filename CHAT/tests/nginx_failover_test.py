"""14.2: isolated Nginx failover, backend lifecycle and crash recovery."""
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


with tempfile.TemporaryDirectory(prefix='chat-failover-') as work:
    redis=None;clients=[];ids=[];gid=None;logs=[];nginx_started=False;nodes_touched=False
    portA,portB,entry,redisPort=[free_port() for _ in range(4)]
    token=uuid.uuid4().hex[:8];nodeA='test142-A-'+token;nodeB='test142-B-'+token
    base=dict(os.environ,CHAT_DB_NAME='chat_module_test',CHAT_DB_USER='chat_module_test',CHAT_DB_PASSWORD='Module02_test_only!',CHAT_REDIS_HOST='127.0.0.1',CHAT_REDIS_PORT=str(redisPort),CHAT_REDIS_PASSWORD='',CHAT_REDIS_PREFIX='test142:'+token+':',CHAT_NODE_A_ID=nodeA,CHAT_NODE_B_ID=nodeB,CHAT_NODE_A_PORT=str(portA),CHAT_NODE_B_PORT=str(portB),CHAT_NODES_RUN_DIR=str(Path(work)/'nodes'))
    proxy_env=dict(os.environ,CHAT_NGINX_PORT=str(entry),CHAT_NGINX_BACKEND_A_PORT=str(portA),CHAT_NGINX_BACKEND_B_PORT=str(portB),CHAT_NGINX_RUN_DIR=str(Path(work)/'nginx'))
    nodes_script=str(root/'deploy/chat-nodes.sh');proxy_script=str(root/'deploy/nginx/chat-nginx.sh')
    def nodes(action,selection='all',env=None,ok=True):
        r=subprocess.run([nodes_script,action,selection],env=env or base,text=True,capture_output=True,timeout=25)
        assert (r.returncode==0)==ok,(r.stdout,r.stderr);return r
    def proxy(action):
        r=subprocess.run([proxy_script,action],env=proxy_env,text=True,capture_output=True,timeout=7)
        assert r.returncode==0,(r.stdout,r.stderr);return r
    def register(c):
        c.send(2);c.until('用户名:');c.send('fail142_'+uuid.uuid4().hex[:10]);c.until('密码:');c.send('demo123')
        out=c.until('请使用该ID登录。');uid=int(re.search(r'用户ID: (\d+)',out).group(1));ids.append(uid);c.until('请选择:');return uid
    def group(c,msg):
        c.send(7);c.until('群ID:');c.send(gid);c.until('消息内容:');c.send(msg);return c.until('请选择:')
    def owner(uid):return sql(f'SELECT server_id FROM User WHERE id={uid}')
    def offline(uid):return sql(f'SELECT state FROM User WHERE id={uid}')=='offline'
    def closed(c):
        assert c.p.wait(timeout=4)==0
        remaining=c.p.stdout.read().decode();assert '服务器连接已关闭' in remaining,c.out+remaining
    try:
        log=open(Path(work)/'redis.log','w+');logs.append(log)
        redis=subprocess.Popen(['redis-server','--bind','127.0.0.1','--port',str(redisPort),'--save','','--appendonly','no','--dir',work],stdout=log,stderr=log)
        eventually(lambda:subprocess.run(['redis-cli','-p',str(redisPort),'ping'],capture_output=True).stdout.strip()==b'PONG','Redis ready')
        nodes_touched=True;nodes('start');nodes('status')
        pidA=(Path(work)/'nodes'/f'{nodeA}.pid').read_text();nodes('start','a');assert (Path(work)/'nodes'/f'{nodeA}.pid').read_text()==pidA
        proxy('start');nginx_started=True
        first=Client(entry);second=Client(entry);clients.extend([first,second]);u1=register(first);u2=register(second);first.login(u1);second.login(u2)
        assert {owner(u1),owner(u2)}=={nodeA,nodeB}
        lost,uidLost,survivor,uidSurvivor=(first,u1,second,u2) if owner(u1)==nodeA else (second,u2,first,u1)
        survivor.send(4);survivor.until('群名称:');survivor.send('故障恢复群');survivor.until('群描述（可留空）:');survivor.send('failover')
        out=survivor.until('选择6刷新群列表。');gid=int(re.search(r'群ID: (\d+)',out).group(1));survivor.until('请选择:')
        lost.send(5);lost.until('群ID:');lost.send(gid);lost.until('加入群成功');lost.until('请选择:')
        print('PASS managed background start/status/idempotence, two C++ clients allocated to distinct nodes',flush=True)
        nodes('stop','a');closed(lost)
        assert offline(uidLost) and owner(uidSurvivor)==nodeB and survivor.p.poll() is None
        assert '已存为离线消息' in survivor.chat(uidLost,'正常退出后的离线单聊')
        assert '离线存储 1' in group(survivor,'正常退出后的离线群聊')
        reconnected=Client(entry);clients.append(reconnected);reconnected.login(uidLost)
        assert owner(uidLost)==nodeB and '[离线单聊]' in reconnected.out and '[离线群聊]' in reconnected.out
        assert '正常退出后的离线单聊' in reconnected.out and '正常退出后的离线群聊' in reconnected.out
        assert '在线连接' in survivor.chat(uidLost,'存活节点继续聊天');reconnected.until('存活节点继续聊天');reconnected.until('请选择:')
        for _ in range(3):
            c=Client(entry);clients.append(c);uid=register(c);c.login(uid);assert owner(uid)==nodeB;c.quit()
        print('PASS graceful node loss disconnects its sessions, preserves survivor, new connections use B, relogin and offline single/group recovery',flush=True)
        nodes('start','a')
        deadline=time.monotonic()+13;restored=None;uidRestored=None
        while time.monotonic()<deadline:
            candidate=Client(entry);clients.append(candidate);uid=register(candidate);candidate.login(uid)
            if owner(uid)==nodeA:restored=candidate;uidRestored=uid;break
            candidate.quit();time.sleep(.35)
        assert restored is not None,'recovered node not selected after fail_timeout'
        assert owner(uidSurvivor)==nodeB and 'Redis跨节点转发' in survivor.chat(uidRestored,'恢复节点重新参与分配')
        restored.until('恢复节点重新参与分配');restored.until('请选择:')
        print('PASS recovered node automatically re-enters new connection distribution without reload; original B connection stays on B',flush=True)
        # SIGKILL is limited to this test-owned node. Nginx cannot repair its DB ownership.
        crashed_pid=int((Path(work)/'nodes'/f'{nodeA}.pid').read_text());os.kill(crashed_pid,9);closed(restored)
        assert owner(uidRestored)==nodeA and not offline(uidRestored)
        eventually(lambda:subprocess.run(['redis-cli','-p',str(redisPort),'--raw','PUBSUB','NUMSUB',base['CHAT_REDIS_PREFIX']+str(uidRestored)],capture_output=True,text=True).stdout.strip().splitlines()[-1]=='0','crashed subscription removed')
        blocked=Client(entry);clients.append(blocked)
        blocked.send(1);blocked.until('用户ID:');blocked.send(uidRestored);blocked.until('密码:');blocked.send('demo123');blocked.until('user already online');blocked.until('请选择:')
        assert owner(uidRestored)==nodeA
        assert '已存为离线消息' in survivor.chat(uidRestored,'强杀后无订阅者仍存离线')
        nodes('restart','a');assert offline(uidRestored) and owner(uidSurvivor)==nodeB
        blocked.login(uidRestored);assert '强杀后无订阅者仍存离线' in blocked.out and owner(uidRestored)==nodeB
        print('PASS crash leaves online residue; login refused until same-ID startup recovery, then existing entry connection can retry and retrieve offline message',flush=True)
        # A PID file naming the test runner must never permit sending a signal.
        guard=Path(work)/'guard';guard.mkdir();(guard/f'{nodeA}.pid').write_text(str(os.getpid())+'\n')
        r=nodes('stop','a',dict(base,CHAT_NODES_RUN_DIR=str(guard)),ok=False);assert 'unrelated' in r.stderr
        nodes('start','a',dict(base,CHAT_NODES_RUN_DIR=str(guard)),ok=False)
        nodes('stop');closed(survivor);closed(reconnected);closed(blocked)
        assert all(offline(uid) for uid in ids)
        proxy('status')
        failed=subprocess.run([str(root/'bin/ChatClient'),'127.0.0.1',str(entry)],input='1\n1\nx\n',text=True,capture_output=True,timeout=5)
        assert '登录成功' not in failed.stdout and ('服务器连接已关闭' in failed.stdout or '请求失败' in failed.stdout),failed.stdout
        print('PASS PID guard and all backends down: proxy remains up but client gets failure, no false login success',flush=True)
        nodes('start')
        # Wait until new upstream attempts are permitted by passive fail_timeout.
        def entry_ready():
            try:
                with socket.create_connection(('127.0.0.1',entry),timeout=1) as s:
                    s.sendall(b'{"msgid":15}\n');return b'login required' in s.recv(1024)
            except OSError:return False
        eventually(entry_ready,'entry recovery after both nodes return')
        final=Client(entry);clients.append(final);final.login(uidSurvivor);assert owner(uidSurvivor) in (nodeA,nodeB);final.quit()
        old_pid=(Path(work)/'nodes'/f'{nodeB}.pid').read_text();nodes('restart','b');nodes('status')
        assert (Path(work)/'nodes'/f'{nodeB}.pid').read_text()!=old_pid
        proxy('stop');nginx_started=False;nodes('stop')
        # Startup failures must not be mistaken for success just because a PID file was written.
        bad=dict(base,CHAT_NODE_A_ID='test142-bad-'+token,CHAT_NODE_B_ID='test142-unused-'+token,CHAT_NODE_A_PORT=str(free_port()),CHAT_NODE_B_PORT=str(free_port()),CHAT_NODES_RUN_DIR=str(Path(work)/'bad'),CHAT_DB_PASSWORD='intentionally-wrong')
        missing=dict(bad);missing.pop('CHAT_DB_PASSWORD',None)
        assert 'Set CHAT_DB_PASSWORD' in nodes('start','a',missing,ok=False).stderr
        result=nodes('start','a',bad,ok=False);assert 'failed to start' in result.stderr
        nodes('status','a',bad,ok=False)
        print('PASS full restart restores entry, failed database startup reported accurately, no forced kill in normal management',flush=True)
    finally:
        for c in clients:
            if c.p.poll() is None:c.p.terminate();c.p.wait(timeout=3)
        if nginx_started:proxy('stop')
        if nodes_touched:nodes('stop')
        if redis and redis.poll() is None:redis.terminate();redis.wait(timeout=5)
        if gid:sql(f'DELETE FROM AllGroup WHERE id={gid}')
        if ids:sql('DELETE FROM User WHERE id IN ('+','.join(map(str,ids))+')')
        for log in logs:log.close()
