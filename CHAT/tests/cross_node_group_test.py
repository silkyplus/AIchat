"""13.3: mixed local/remote/offline group routing with real C++ clients."""
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


with tempfile.TemporaryDirectory(prefix='chat-crossgroup-') as work:
    redis=None;servers=[];clients=[];peers=[];ids=[];logs=[];gid=None;namespace='test133:'+uuid.uuid4().hex+':'
    portA,portB,redisPort=[free_port() for _ in range(3)]
    base=dict(os.environ,CHAT_DB_NAME='chat_module_test',CHAT_DB_USER='chat_module_test',CHAT_DB_PASSWORD='Module02_test_only!',CHAT_REDIS_HOST='127.0.0.1',CHAT_REDIS_PORT=str(redisPort),CHAT_REDIS_PASSWORD='',CHAT_REDIS_PREFIX=namespace)
    def cli(*args):return subprocess.run(['redis-cli','-p',str(redisPort),'--raw',*map(str,args)],text=True,capture_output=True,check=True).stdout.strip()
    def start(port,node):
        log=open(Path(work)/(node+'.log'),'a+');logs.append(log)
        p=subprocess.Popen([str(root/'bin/ChatServer'),str(port)],env=dict(base,CHAT_SERVER_ID=node),stdout=log,stderr=log);servers.append(p)
        def ready():
            assert p.poll() is None,Path(log.name).read_text()
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=.1):return True
            except OSError:return False
        eventually(ready,'server startup');return p
    def join(c):
        c.send(5);c.until('群ID:');c.send(gid);c.until('加入群成功');c.until('请选择:')
    def group(c,msg):
        c.send(7);c.until('群ID:');c.send(gid);c.until('消息内容:');c.send(msg);return c.until('请选择:')
    def receive(c,msg):
        c.until(msg);c.until('请选择:');assert c.out.count(msg)==1,c.out
    try:
        log=open(Path(work)/'redis.log','w+');logs.append(log)
        redis=subprocess.Popen(['redis-server','--bind','127.0.0.1','--port',str(redisPort),'--save','','--appendonly','no','--dir',work],stdout=log,stderr=log)
        eventually(lambda:subprocess.run(['redis-cli','-p',str(redisPort),'ping'],capture_output=True).stdout.strip()==b'PONG','Redis startup')
        token=uuid.uuid4().hex[:8];nodeA='test133-A-'+token;nodeB='test133-B-'+token
        sa=start(portA,nodeA);sb=start(portB,nodeB)
        setup=Peer(portA);peers.append(setup)
        for _ in range(6):
            ack=setup.request(dict(msgid=2,name='group133_'+uuid.uuid4().hex[:12],password='demo123'));assert ack['errno']==0;ids.append(ack['id'])
        a=Client(portA);b=Client(portB);c=Client(portA);clients.extend([a,b,c]);a.login(ids[0]);b.login(ids[1]);c.login(ids[2])
        a.send(4);a.until('群名称:');a.send('跨节点中文群');a.until('群描述（可留空）:');a.send('mixed routes')
        out=a.until('选择6刷新群列表。');gid=int(re.search(r'群ID: (\d+)',out).group(1));a.until('请选择:')
        join(b);join(c)
        d=Peer(portB);peers.append(d);assert d.request(dict(msgid=1,id=ids[3],password='demo123'))['errno']==0
        assert d.request(dict(msgid=11,groupid=gid))['errno']==0;d.close();peers.remove(d)
        eventually(lambda:sql(f'SELECT state FROM User WHERE id={ids[3]}')=='offline','D offline')
        assert setup.request(dict(msgid=1,id=ids[4],password='demo123'))['errno']==0
        assert setup.request(dict(msgid=13,groupid=gid,msg='outsider'))['errno']==1
        assert setup.request(dict(msgid=13,groupid=gid,id=ids[0],msg='spoof'))['errno']==1
        assert setup.request(dict(msgid=13,groupid=2147483647,msg='absent'))['errno']==1
        a.send(6);out=a.until('请选择:');assert '跨节点中文群' in out and 'offline' in out and 'creator' in out
        out=group(a,'A混合路由第一条')
        assert '在线提交 2，离线存储 1，失败 0' in out and '本节点 1，Redis跨节点 1' in out and '[群聊]' not in out,out
        receive(b,'A混合路由第一条');receive(c,'A混合路由第一条')
        assert sql(f'SELECT COUNT(*) FROM OfflineMessage WHERE userid={ids[3]}')=='1'
        assert sql('SELECT COUNT(*) FROM OfflineMessage WHERE userid IN ('+','.join(map(str,ids[:3]))+')')=='0'
        out=group(b,'B跨节点群回复');assert '在线提交 2，离线存储 1，失败 0' in out and '本节点 0，Redis跨节点 2' in out,out
        receive(a,'B跨节点群回复');receive(c,'B跨节点群回复')
        print('PASS mixed local/remote/offline fanout, totals, reverse group route, one copy per recipient, no self echo and membership/sender checks',flush=True)
        # Invalid Redis messages must not break later legitimate group deliveries.
        for data in [dict(msgid=13,id=ids[0],toid=ids[1],msg='badgroup',time='t'),dict(msgid=13,id=ids[1],toid=ids[1],groupid=gid,msg='badgroup',time='t'),dict(msgid=13,id=ids[0],toid=ids[0],groupid=gid,msg='badgroup',time='t')]:
            cli('PUBLISH',namespace+str(ids[1]),json.dumps(data))
        f=Peer(portA);peers.append(f);assert f.request(dict(msgid=1,id=ids[5],password='demo123'))['errno']==0
        assert f.request(dict(msgid=11,groupid=gid))['errno']==0;f.close();peers.remove(f)
        eventually(lambda:sql(f'SELECT state FROM User WHERE id={ids[5]}')=='offline','F offline')
        sql(f"UPDATE User SET state='online',server_id='test133-stale' WHERE id={ids[5]}")
        out=group(a,'无订阅者也存离线');assert '在线提交 2，离线存储 2，失败 0' in out,out
        receive(b,'无订阅者也存离线');receive(c,'无订阅者也存离线');assert 'badgroup' not in b.out
        assert sql(f'SELECT COUNT(*) FROM OfflineMessage WHERE userid={ids[5]}')=='1'
        sql(f"UPDATE User SET state='offline',server_id=NULL WHERE id={ids[5]}")
        d2=Client(portB);clients.append(d2);d2.login(ids[3])
        assert '本次收到 3 条离线消息' in d2.out and '[离线群聊]' in d2.out and f'群 {gid}' in d2.out
        assert d2.out.index('A混合路由第一条')<d2.out.index('B跨节点群回复')<d2.out.index('无订阅者也存离线')
        assert sql(f'SELECT COUNT(*) FROM OfflineMessage WHERE userid={ids[3]}')=='0'
        d2.quit();eventually(lambda:sql(f'SELECT state FROM User WHERE id={ids[3]}')=='offline','D quit')
        print('PASS malformed routed groups ignored, zero-subscriber offline fallback, ordered offline group display after login',flush=True)
        sb.terminate();assert sb.wait(timeout=7)==0;assert b.p.wait(timeout=3)==0
        assert sql(f'SELECT state FROM User WHERE id={ids[1]}')=='offline'
        assert sql(f'SELECT state FROM User WHERE id={ids[0]}')=='online'
        assert cli('PUBSUB','NUMSUB',namespace+str(ids[1])).splitlines()[-1]=='0'
        sb=start(portB,nodeB);b2=Client(portB);clients.append(b2);b2.login(ids[1])
        out=group(b2,'B重启后的群回复');assert '在线提交 2，离线存储 2，失败 0' in out and 'Redis跨节点 2' in out,out
        receive(a,'B重启后的群回复');receive(c,'B重启后的群回复')
        print('PASS node shutdown unsubscribes its users, isolates other node and restart/relogin restores group routing',flush=True)
        redis.terminate();assert redis.wait(timeout=5)==0;redis=None;time.sleep(.2)
        out=group(a,'Redis中断部分群发')
        assert '在线提交 1，离线存储 2，失败 1' in out and '本节点 1，Redis跨节点 0' in out and 'some recipients failed' in out,out
        receive(c,'Redis中断部分群发')
        assert sql(f'SELECT COUNT(*) FROM OfflineMessage WHERE userid={ids[1]}')=='0'
        assert sql(f'SELECT COUNT(*) FROM OfflineMessage WHERE userid={ids[3]}')=='2'
        assert sql(f'SELECT COUNT(*) FROM OfflineMessage WHERE userid={ids[5]}')=='3'
        print('PASS Redis outage partial-failure totals preserve local delivery and offline storage, no ambiguous remote fallback',flush=True)
        a.quit();c.quit();b2.quit()
    finally:
        for client in clients:
            if client.p.poll() is None:client.p.terminate();client.p.wait(timeout=3)
        for peer in peers:
            try:peer.close()
            except OSError:pass
        for p in servers:
            if p.poll() is None:p.terminate();p.wait(timeout=8)
        if redis and redis.poll() is None:redis.terminate();redis.wait(timeout=5)
        if gid:sql(f'DELETE FROM AllGroup WHERE id={gid}')
        if ids:sql('DELETE FROM User WHERE id IN ('+','.join(map(str,ids))+')')
        for log in logs:log.close()
