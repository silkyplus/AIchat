"""12.4: group menus and online/offline delivery with real C++ clients."""
import os, subprocess, socket, json, time, uuid, select, codecs, tempfile
from pathlib import Path
root = Path(__file__).resolve().parents[1]
env = dict(os.environ, CHAT_DB_NAME='chat_module_test', CHAT_DB_USER='chat_module_test', CHAT_DB_PASSWORD='Module02_test_only!', CHAT_SERVER_ID='test-client-group-16143')
config = '[client]\nuser=chat_module_test\npassword=Module02_test_only!\nhost=127.0.0.1\nprotocol=tcp\n'
def sql(q):
    return subprocess.run(['mysql','--defaults-file=/dev/stdin','-N','chat_module_test','-e',q], input=config, text=True, capture_output=True, check=True).stdout.strip()
class Client:
    def __init__(self):
        self.p = subprocess.Popen([str(root/'bin/ChatClient'),'127.0.0.1','16143'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
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

log=tempfile.TemporaryFile();server=None;clients=[];ids=[];gid=None
try:
    server=subprocess.Popen([str(root/'bin/ChatServer'),'16143'],env=env,stdout=log,stderr=log)
    time.sleep(.3);assert server.poll() is None
    with socket.create_connection(('127.0.0.1',16143),timeout=5) as sock:
        f=sock.makefile('rb')
        def req(data):
            sock.sendall((json.dumps(data)+'\n').encode());return json.loads(f.readline())
        assert req(dict(msgid=17))['errmsg']=='login required'
        for _ in range(3):
            ack=req(dict(msgid=2,name='c124_'+uuid.uuid4().hex[:12],password='demo123'));assert ack['errno']==0;ids.append(ack['id'])
        assert req(dict(msgid=1,id=ids[2],password='demo123'))['errno']==0
        a=Client();clients.append(a);b=Client();clients.append(b);a.login(ids[0]);b.login(ids[1])
        b.send(6);b.until('暂未加入任何群');b.until('请选择:')
        a.send(4);a.until('群名称:');a.send('');a.until('群名称需为');a.until('请选择:')
        a.send(4);a.until('群名称:');a.send('C++中文群');a.until('群描述（可留空）:');a.send('一起学习 网络编程')
        out=a.until('选择6刷新群列表。');gid=int(__import__('re').search(r'群ID: (\d+)',out).group(1));a.until('请选择:')
        a.send(6);out=a.until('请选择:');assert 'C++中文群' in out and '一起学习 网络编程' in out and '我的角色: creator' in out
        # Only creator belongs: valid empty fanout, no self echo.
        a.send(7);a.until('群ID:');a.send(gid);a.until('消息内容:');a.send('只有我');out=a.until('请选择:');assert '在线提交 0，离线存储 0，失败 0' in out and '[群聊]' not in out
        assert req(dict(msgid=13,groupid=gid,msg='outsider'))['errno']!=0
        assert req(dict(msgid=17))['groups']==[]
        b.send(5);b.until('群ID:');b.send('bad');b.until('ID必须');b.until('请选择:')
        b.send(5);b.until('群ID:');b.send(2147483647);b.until('group or user does not exist');b.until('请选择:')
        b.send(5);b.until('群ID:');b.send(gid);b.until('加入群成功');b.until('请选择:')
        b.send(5);b.until('群ID:');b.send(gid);b.until('already a group member');b.until('请选择:')
        b.send(6);out=b.until('请选择:');assert '我的角色: normal' in out and 'creator' in out and f'成员ID: {ids[0]}' in out and f'成员ID: {ids[1]}' in out
        print('PASS create/join/list menus, role/member display, duplicate/nonexistent group, unauthenticated and outsider rejection')
        a.send(7);a.until('群ID:');a.send(gid);a.until('消息内容:');a.send('在线群聊中文');out=a.until('请选择:');assert '在线提交 1，离线存储 0，失败 0' in out
        b.until('在线群聊中文');b.until('请选择:')
        b.send(7);b.until('群ID:');b.send(gid);b.until('消息内容:');b.send('群聊回复');b.until('请选择:');a.until('群聊回复');a.until('请选择:')
        b.send(0);assert b.p.wait(timeout=3)==0
        end=time.monotonic()+3
        while sql(f'SELECT state FROM User WHERE id={ids[1]}')!='offline':
            assert time.monotonic()<end;time.sleep(.05)
        a.send(6);out=a.until('请选择:');assert 'offline' in out
        a.send(7);a.until('群ID:');a.send(gid);a.until('消息内容:');a.send('离线群聊中文');out=a.until('请选择:');assert '在线提交 0，离线存储 1，失败 0' in out
        b2=Client();clients.append(b2);b2.login(ids[1]);assert '[离线群聊]' in b2.out and '离线群聊中文' in b2.out and f'群 {gid}' in b2.out
        assert sql(f'SELECT COUNT(*) FROM OfflineMessage WHERE userid={ids[1]}')=='0'
        b2.send(0);assert b2.p.wait(timeout=3)==0
        a.send(0);assert a.p.wait(timeout=3)==0
        print('PASS online group fanout/reply, no self echo, refreshed offline member, stored group message and login display')
finally:
    for c in clients:
        if c.p.poll() is None:c.p.terminate();c.p.wait(timeout=3)
    if server and server.poll() is None:server.terminate();server.wait(timeout=8)
    if gid:sql(f'DELETE FROM AllGroup WHERE id={gid}')
    if ids:sql('DELETE FROM User WHERE id IN ('+','.join(map(str,ids))+')')
    log.close()
