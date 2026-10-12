"""12.2: exercise two real C++ clients against an isolated test server."""
import os, subprocess, socket, json, time, uuid, select, codecs, tempfile
from pathlib import Path
root = Path(__file__).resolve().parents[1]
env = dict(os.environ, CHAT_DB_NAME='chat_module_test', CHAT_DB_USER='chat_module_test', CHAT_DB_PASSWORD='Module02_test_only!', CHAT_SERVER_ID='test-client-chat-16141')
config = '[client]\nuser=chat_module_test\npassword=Module02_test_only!\nhost=127.0.0.1\nprotocol=tcp\n'
def sql(q):
    return subprocess.run(['mysql','--defaults-file=/dev/stdin','-N','chat_module_test','-e',q], input=config, text=True, capture_output=True, check=True).stdout.strip()
class Client:
    def __init__(self):
        self.p = subprocess.Popen([str(root/'bin/ChatClient'),'127.0.0.1','16141'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.out=''; self.pos=0; self.decoder=codecs.getincrementaldecoder('utf-8')()
        self.until('请选择:')
    def send(self, s):
        self.p.stdin.write((str(s)+'\n').encode()); self.p.stdin.flush()
    def until(self, token):
        end=time.monotonic()+6
        while token not in self.out[self.pos:]:
            assert time.monotonic()<end, self.out
            if select.select([self.p.stdout],[],[],.1)[0]:
                b=os.read(self.p.stdout.fileno(),8192); assert b, self.out
                self.out+=self.decoder.decode(b)
        i=self.out.index(token,self.pos)+len(token); result=self.out[self.pos:i]; self.pos=i; return result
    def login(self, uid):
        self.send(1);self.until('用户ID:');self.send(uid);self.until('密码:');self.send('demo123');self.until('登录成功');self.until('请选择:')
    def chat(self, uid, msg):
        self.send(3);self.until('接收者ID:');self.send(uid);self.until('消息内容:');self.send(msg)
log=tempfile.TemporaryFile(); server=None; clients=[]; ids=[]
try:
    server=subprocess.Popen([str(root/'bin/ChatServer'),'16141'],env=env,stdout=log,stderr=log)
    time.sleep(.3);assert server.poll() is None
    with socket.create_connection(('127.0.0.1',16141)) as sock:
        f=sock.makefile('rb')
        sock.sendall(b'{"msgid":15}\n');assert json.loads(f.readline())['errmsg']=='login required'
        for _ in range(2):
            req=dict(msgid=2,name='c122_'+uuid.uuid4().hex[:12],password='demo123')
            sock.sendall((json.dumps(req)+'\n').encode());ack=json.loads(f.readline());assert ack['errno']==0;ids.append(ack['id'])
    a=Client();clients.append(a);b=Client();clients.append(b);a.login(ids[0]);b.login(ids[1])
    a.send(2);a.until('好友ID:');a.send(ids[1]);a.until('添加好友成功');a.until('请选择:')
    b.send(1);out=b.until('请选择:');assert 'ID: '+str(ids[0]) in out and 'online' in out
    a.send(2);a.until('好友ID:');a.send(ids[1]);a.until('already friends');a.until('请选择:')
    a.send(2);a.until('好友ID:');a.send(ids[0]);a.until('cannot add yourself');a.until('请选择:')
    print('PASS authenticated refresh, bidirectional friendship, duplicate/self rejection')
    a.chat(ids[1],'你好，客户端B');a.until('消息已提交给在线连接');a.until('请选择:');b.until('你好，客户端B');b.until('请选择:')
    b.chat(ids[0],'收到，这是B');b.until('消息已提交给在线连接');b.until('请选择:');a.until('收到，这是B');a.until('请选择:')
    print('PASS two real C++ clients exchange UTF-8 messages without recipient pressing Enter')
    b.send(0);assert b.p.wait(timeout=3)==0
    end=time.monotonic()+3
    while sql('SELECT state FROM User WHERE id='+str(ids[1]))!='offline':
        assert time.monotonic()<end;time.sleep(.05)
    a.send(1);out=a.until('请选择:');assert 'offline' in out and 'ID: '+str(ids[1]) in out
    a.chat(ids[1],'离线测试');a.until('消息已存为离线消息');a.until('请选择:')
    assert sql('SELECT COUNT(*) FROM OfflineMessage WHERE userid='+str(ids[1]))=='1'
    a.chat(2147483647,'不存在的接收者');a.until('操作失败:');a.until('请选择:')
    a.send(0);assert a.p.wait(timeout=3)==0
    print('PASS refreshed offline state, persisted offline send, nonexistent recipient, clean exit')
finally:
    for c in clients:
        if c.p.poll() is None:c.p.terminate();c.p.wait(timeout=3)
    if server and server.poll() is None:server.terminate();server.wait(timeout=8)
    if ids:sql('DELETE FROM User WHERE id IN ('+','.join(map(str,ids))+')')
    log.close()
