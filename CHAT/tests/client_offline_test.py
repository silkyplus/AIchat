"""12.3: real offline single/group messages, pagination and display errors."""
import os, subprocess, socket, json, time, uuid, tempfile, threading
from pathlib import Path
root=Path(__file__).resolve().parents[1]
config='[client]\nuser=chat_module_test\npassword=Module02_test_only!\nhost=127.0.0.1\nprotocol=tcp\n'
def sql(q):
    return subprocess.run(['mysql','--defaults-file=/dev/stdin','-N','chat_module_test','-e',q],input=config,text=True,capture_output=True,check=True).stdout.strip()
env=dict(os.environ,CHAT_DB_NAME='chat_module_test',CHAT_DB_USER='chat_module_test',CHAT_DB_PASSWORD='Module02_test_only!',CHAT_SERVER_ID='test-offline-client-16142')
log=tempfile.TemporaryFile();p=None;ids=[];gid=None;sockets=[]
def connect():
    s=socket.create_connection(('127.0.0.1',16142),timeout=5);sockets.append(s);return s,s.makefile('rb')
def request(c,req):
    s,f=c;s.sendall((json.dumps(req,ensure_ascii=False)+'\n').encode());ack=json.loads(f.readline());assert ack['errno']==0,ack;return ack
def client_login(uid):
    result=subprocess.run([str(root/'bin/ChatClient'),'127.0.0.1','16142'],input=f'1\n{uid}\ndemo123\n0\n',text=True,capture_output=True,timeout=7)
    assert result.returncode==0 and '登录成功' in result.stdout,result.stdout
    end=time.monotonic()+3
    while sql(f'SELECT state FROM User WHERE id={uid}')!='offline':
        assert time.monotonic()<end;time.sleep(.05)
    return result.stdout
try:
    p=subprocess.Popen([str(root/'bin/ChatServer'),'16142'],env=env,stdout=log,stderr=log);time.sleep(.3);assert p.poll() is None
    a=connect();b=connect()
    for c in (a,b):ids.append(request(c,dict(msgid=2,name='offline123_'+uuid.uuid4().hex[:10],password='demo123'))['id'])
    request(a,dict(msgid=1,id=ids[0],password='demo123'))
    gid=request(a,dict(msgid=9,groupname='offline_group',groupdesc='test'))['groupid']
    request(b,dict(msgid=1,id=ids[1],password='demo123'));request(b,dict(msgid=11,groupid=gid))
    b[0].shutdown(socket.SHUT_RDWR);b[0].close()
    end=time.monotonic()+3
    while sql(f'SELECT state FROM User WHERE id={ids[1]}')!='offline':
        assert time.monotonic()<end;time.sleep(.05)
    request(a,dict(msgid=5,toid=ids[1],msg='先来的单聊中文'))
    request(a,dict(msgid=13,groupid=gid,msg='后来的群聊中文'))
    out=client_login(ids[1]);assert '本次收到 2 条离线消息' in out and '[离线单聊]' in out and '[离线群聊]' in out and f'群 {gid}' in out and ' UTC' in out,out
    assert out.index('先来的单聊中文')<out.index('后来的群聊中文')
    assert sql(f'SELECT COUNT(*) FROM OfflineMessage WHERE userid={ids[1]}')=='0'
    assert '暂无离线消息' in client_login(ids[1])
    print('PASS real offline single/group display, order, timestamps and no repeated delivery')
    for i in range(101):request(a,dict(msgid=5,toid=ids[1],msg=f'batch-{i:03}'))
    out=client_login(ids[1]);assert '本次收到 100 条离线消息' in out and '可能还有离线消息' in out and 'batch-099' in out and 'batch-100' not in out
    assert sql(f'SELECT COUNT(*) FROM OfflineMessage WHERE userid={ids[1]}')=='1'
    out=client_login(ids[1]);assert '本次收到 1 条离线消息' in out and 'batch-100' in out
    print('PASS 101 messages fetched/displayed as 100 + 1 with batch hint')
finally:
    for s in sockets:s.close()
    if p and p.poll() is None:p.terminate();p.wait(timeout=8)
    if gid:sql(f'DELETE FROM AllGroup WHERE id={gid}')
    if ids:sql('DELETE FROM User WHERE id IN ('+','.join(map(str,ids))+')')
    log.close()

def mock(extra, expected):
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1];errors=[]
    def serve():
        try:
            conn,_=listener.accept()
            with conn:
                conn.settimeout(5);conn.makefile('rb').readline()
                ack=dict(msgid=4,errno=0,id=1,name='mock');ack.update(extra)
                conn.sendall((json.dumps(ack)+'\n').encode())
                # 保持连接直到客户端完成菜单退出。
                while conn.recv(1024):pass
        except Exception as e:errors.append(e)
        finally:listener.close()
    t=threading.Thread(target=serve);t.start()
    out=subprocess.run([str(root/'bin/ChatClient'),'127.0.0.1',str(port)],input='1\n1\npw\n0\n',text=True,capture_output=True,timeout=7).stdout
    t.join(timeout=6);assert not t.is_alive() and not errors,errors
    for token in expected:assert token in out,out
    return out
out=mock(dict(offlinemsg=[],offline_error='unavailable'),['登录成功','离线消息读取失败']);assert '暂无离线消息' not in out
mock(dict(offlinemsg='invalid'),['服务器未返回有效的离线消息列表'])
mock(dict(offlinemsg=[dict(msgid=99),dict(msgid=5,id=2,msg='valid-after-invalid')]),['格式异常或暂不支持的离线消息','valid-after-invalid'])
print('PASS retrieval error, invalid list and per-message error isolation')
