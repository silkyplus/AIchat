"""13.2: private Redis, two ChatServers, real C++ clients and protocol failure tests."""
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

with tempfile.TemporaryDirectory(prefix='chat-crossnode-') as work:
    redis=None;servers=[];clients=[];peers=[];ids=[];logs=[];prefix='test132:'+uuid.uuid4().hex+':'
    portA,portB,portC,redisPort=[free_port() for _ in range(4)]
    base=dict(os.environ,CHAT_DB_NAME='chat_module_test',CHAT_DB_USER='chat_module_test',CHAT_DB_PASSWORD='Module02_test_only!',CHAT_REDIS_HOST='127.0.0.1',CHAT_REDIS_PORT=str(redisPort),CHAT_REDIS_PASSWORD='',CHAT_REDIS_PREFIX=prefix)
    def cli(*args):
        return subprocess.run(['redis-cli','-p',str(redisPort),'--raw',*map(str,args)],text=True,capture_output=True,check=True).stdout.strip()
    def subscribers(uid):return cli('PUBSUB','NUMSUB',prefix+str(uid)).splitlines()[-1]
    def start(port, node, override=None, expect_ok=True):
        log=open(Path(work)/(node+'.log'),'w+');logs.append(log)
        env=dict(base,CHAT_SERVER_ID=node)
        if override:env.update(override)
        p=subprocess.Popen([str(root/'bin/ChatServer'),str(port)],env=env,stdout=log,stderr=log);servers.append(p)
        if expect_ok:
            def ready():
                assert p.poll() is None,Path(log.name).read_text()
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=.1):return True
                except OSError:return False
            eventually(ready,'server startup')
        else:assert p.wait(timeout=6)==1
        return p
    try:
        log=open(Path(work)/'redis.log','w+');logs.append(log)
        redis=subprocess.Popen(['redis-server','--bind','127.0.0.1','--port',str(redisPort),'--save','','--appendonly','no','--dir',work],stdout=log,stderr=log)
        eventually(lambda: subprocess.run(['redis-cli','-p',str(redisPort),'ping'],capture_output=True).stdout.strip()==b'PONG','Redis startup')
        token=uuid.uuid4().hex[:8];nodeA='test132-A-'+token;nodeB='test132-B-'+token
        sa=start(portA,nodeA);sb=start(portB,nodeB)
        pa=Peer(portA);peers.append(pa)
        for _ in range(5):
            ack=pa.request(dict(msgid=2,name='node132_'+uuid.uuid4().hex[:12],password='demo123'));assert ack['errno']==0,ack;ids.append(ack['id'])
        a=Client(portA);b=Client(portB);clients.extend([a,b]);a.login(ids[0]);b.login(ids[1])
        assert subscribers(ids[0])=='1' and subscribers(ids[1])=='1'
        assert sql(f'SELECT server_id FROM User WHERE id={ids[0]}')==nodeA
        assert sql(f'SELECT server_id FROM User WHERE id={ids[1]}')==nodeB
        duplicate=pa.request(dict(msgid=1,id=ids[1],password='demo123'));assert duplicate['errno']==2
        assert subscribers(ids[1])=='1'
        assert 'Redis跨节点转发' in a.chat(ids[1],'A到B，跨节点中文')
        b.until('A到B，跨节点中文');b.until('请选择:')
        assert 'Redis跨节点转发' in b.chat(ids[0],'B回复A')
        a.until('B回复A');a.until('请选择:')
        assert sql('SELECT COUNT(*) FROM OfflineMessage WHERE userid IN ('+','.join(map(str,ids[:2]))+')')=='0'
        print('PASS two real C++ clients bidirectional cross-node Chinese chat, ownership, one subscription and duplicate login refusal',flush=True)
        # Wrong channel destination and invalid JSON must be ignored without harming later traffic.
        cli('PUBLISH',prefix+str(ids[1]),json.dumps(dict(msgid=5,id=ids[0],toid=ids[0],msg='bad-target',time='test')))
        cli('PUBLISH',prefix+str(ids[1]),'not-json')
        assert 'Redis跨节点转发' in a.chat(ids[1],'valid-after-invalid')
        b.until('valid-after-invalid');assert 'bad-target' not in b.out;b.until('请选择:')
        b.quit();eventually(lambda:sql(f'SELECT state FROM User WHERE id={ids[1]}')=='offline','disconnect offline')
        assert subscribers(ids[1])=='0'
        assert '已存为离线消息' in a.chat(ids[1],'断线后的离线单聊')
        b2=Client(portA);clients.append(b2);b2.login(ids[1]);assert '断线后的离线单聊' in b2.out and '[离线单聊]' in b2.out
        assert subscribers(ids[1])=='1'
        assert '在线连接' in a.chat(ids[1],'同节点仍正常');b2.until('同节点仍正常');b2.until('请选择:')
        print('PASS invalid Redis payload isolation, unsubscribe on disconnect, MySQL offline delivery, migration to other node and local fast path',flush=True)
        # Stale online without subscriber: do not falsely report successful remote delivery.
        sql(f"UPDATE User SET state='online',server_id='test132-stale' WHERE id={ids[2]}")
        assert '已存为离线消息' in a.chat(ids[2],'无订阅者回退')
        assert sql(f'SELECT COUNT(*) FROM OfflineMessage WHERE userid={ids[2]}')=='1'
        sql(f"UPDATE User SET state='offline',server_id=NULL WHERE id={ids[2]}")
        assert pa.request(dict(msgid=1,id=ids[2],password='demo123'))['errno']==0
        spoof=pa.request(dict(msgid=5,id=ids[0],toid=ids[1],msg='spoof'));assert spoof['errno']==1
        print('PASS stale online/zero subscriber offline fallback and sender identity validation',flush=True)
        # Subscribe ACL failure after online acquisition must roll back connection and DB.
        sc=start(portC,'test132-C-'+token)
        cli('ACL','SETUSER','default','-subscribe')
        pc=Peer(portC);peers.append(pc)
        ack=pc.request(dict(msgid=1,id=ids[3],password='demo123'))
        assert ack['errno']==1 and 'subscription failed' in ack['errmsg'],ack
        assert sql(f'SELECT CONCAT(state,":",COALESCE(server_id,"NULL")) FROM User WHERE id={ids[3]}')=='offline:NULL'
        assert subscribers(ids[3])=='0'
        assert pc.request(dict(msgid=7,friendid=ids[0]))['errmsg']=='login required'
        cli('ACL','SETUSER','default','+subscribe');sc.terminate();assert sc.wait(timeout=7)==0
        print('PASS Redis subscribe failure rolls back login identity/DB and reports failure',flush=True)
        # Graceful shutdown only resets its own users and drops its subscriber connection.
        b2.quit();eventually(lambda:sql(f'SELECT state FROM User WHERE id={ids[1]}')=='offline','migration disconnect')
        beforeStop=Client(portB);clients.append(beforeStop);beforeStop.login(ids[1])
        sb.terminate();assert sb.wait(timeout=7)==0
        assert beforeStop.p.wait(timeout=3)==0
        assert sql(f'SELECT state FROM User WHERE id={ids[0]}')=='online'
        assert sql(f'SELECT state FROM User WHERE id={ids[1]}')=='offline'
        assert subscribers(ids[1])=='0'
        sb=start(portB,nodeB)
        b3=Client(portB);clients.append(b3);b3.login(ids[1]);assert subscribers(ids[1])=='1'
        assert 'Redis跨节点转发' in a.chat(ids[1],'节点重启后正常');b3.until('节点重启后正常');b3.until('请选择:')
        print('PASS node shutdown isolation and restart/relogin restores cross-node routing',flush=True)
        redis.terminate();assert redis.wait(timeout=5)==0;redis=None;time.sleep(.2)
        assert 'routing unavailable' in a.chat(ids[1],'Redis断线失败反馈')
        assert sql(f'SELECT COUNT(*) FROM OfflineMessage WHERE userid={ids[1]}')=='0'
        assert '在线连接' in a.chat(ids[2],'Redis断线本地仍可发')
        routed=json.loads(pa.f.readline());assert routed['msg']=='Redis断线本地仍可发'
        assert '已存为离线消息' in a.chat(ids[4],'Redis断线离线仍存储')
        lost=pa.request(dict(msgid=2,name='node132_'+uuid.uuid4().hex[:12],password='demo123'));assert lost['errno']==0;ids.append(lost['id'])
        afterLoss=Peer(portA);peers.append(afterLoss)
        ack=afterLoss.request(dict(msgid=1,id=ids[-1],password='demo123'));assert ack['errno']==1
        assert sql(f'SELECT state FROM User WHERE id={ids[-1]}')=='offline'
        start(portC,'test132-unavailable-'+token,expect_ok=False)
        print('PASS Redis outage explicit remote failure, local/offline paths survive, unavailable Redis refuses server startup',flush=True)
    finally:
        for c in clients:
            if c.p.poll() is None:c.p.terminate();c.p.wait(timeout=3)
        for peer in peers:
            try:peer.close()
            except OSError:pass
        for p in servers:
            if p.poll() is None:p.terminate();p.wait(timeout=8)
        if redis and redis.poll() is None:redis.terminate();redis.wait(timeout=5)
        if ids:sql('DELETE FROM User WHERE id IN ('+','.join(map(str,ids))+')')
        for log in logs:log.close()
