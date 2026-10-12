# Run after building and initializing the isolated chat_module_test database.
import os,subprocess,socket,json,time,uuid,select,re,threading,tempfile
from pathlib import Path
root=str(Path(__file__).resolve().parents[1])
env=dict(os.environ,CHAT_DB_NAME='chat_module_test',CHAT_DB_USER='chat_module_test',CHAT_DB_PASSWORD='Module02_test_only!',CHAT_SERVER_ID='test-client-16140')
config='[client]\nuser=chat_module_test\npassword=Module02_test_only!\nhost=127.0.0.1\nprotocol=tcp\n'
def sql(q):
    return subprocess.run(['mysql','--defaults-file=/dev/stdin','-N','chat_module_test','-e',q],input=config,text=True,capture_output=True,check=True).stdout.strip()
log=tempfile.TemporaryFile(mode='w+')
p=subprocess.Popen([root+'/bin/ChatServer','16140'],env=env,stdout=log,stderr=log)
name='client_'+uuid.uuid4().hex[:10];uid=None;clients=[];allout='';cursor=0
try:
    time.sleep(.3);assert p.poll() is None
    c=subprocess.Popen([root+'/bin/ChatClient','127.0.0.1','16140'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT);clients.append(c)
    def until(token,timeout=5):
        global allout,cursor
        end=time.monotonic()+timeout
        while token not in allout[cursor:]:
            assert time.monotonic()<end,allout
            ready,_,_=select.select([c.stdout],[],[],.1)
            if ready:
                chunk=os.read(c.stdout.fileno(),8192)
                assert chunk,allout
                # 每次解码允许中间UTF-8边界，测试实际输出块足够小，不依赖中文日志做数据协议。
                allout+=chunk.decode('utf-8',errors='replace')
        i=allout.index(token,cursor);result=allout[cursor:i+len(token)];cursor=i+len(token);return result
    def write(t):c.stdin.write((t+'\n').encode());c.stdin.flush()
    until('请选择:');write('bad');until('无效选项');until('请选择:')
    write('1');until('用户ID:');write('abc');until('用户ID必须');until('请选择:')
    write('2');until('用户名:');write(name);until('密码:');write('demo123')
    result=until('请使用该ID登录。');uid=int(re.search(r'用户ID: (\d+)',result).group(1));until('请选择:')
    print('PASS actual C++ menu registration and invalid input handling')
    write('2');until('用户名:');write(name);until('密码:');write('demo123');until('username already exists');until('请选择:')
    write('1');until('用户ID:');write(str(uid));until('密码:');write('wrong');until('invalid user id or password');until('请选择:')
    write('1');until('用户ID:');write(str(uid));until('密码:');write('demo123');until('登录成功');until('请选择:')
    assert sql('SELECT state FROM User WHERE id='+str(uid))=='online'
    write('0');c.stdin.close();assert c.wait(timeout=4)==0
    untiltime=time.monotonic()+4
    while sql('SELECT state FROM User WHERE id='+str(uid))!='offline':
        assert time.monotonic()<untiltime;time.sleep(.05)
    print('PASS duplicate registration, wrong password, login and exit/offline cleanup')
    with socket.create_connection(('127.0.0.1',16140),timeout=3) as s:
        line=b'{"msgid":7,"friendid":1}\n';f=s.makefile('rb')
        s.sendall(line[:10]);time.sleep(.1)
        s.settimeout(.1)
        try:s.recv(1);raise AssertionError('partial request parsed too early')
        except socket.timeout:pass
        s.settimeout(3);s.sendall(line[10:]);assert json.loads(f.readline())['msgid']==8
        s.sendall(line+line);assert json.loads(f.readline())['msgid']==8 and json.loads(f.readline())['msgid']==8
        s.sendall(b'not-json\n'+line);assert json.loads(f.readline())['msgid']==0 and json.loads(f.readline())['msgid']==8
        s.sendall(b'\r\n'+line.replace(b'\n',b'\r\n'));assert json.loads(f.readline())['msgid']==8
    with socket.create_connection(('127.0.0.1',16140),timeout=3) as s:
        s.sendall(b'x'*(64*1024+1))
        try:assert s.recv(1)==b''
        except ConnectionResetError:pass
    print('PASS server split/coalesced frames, CRLF, bad-line recovery and frame size limit')
    # 主线程正在等待菜单输入时，服务端退出也能让客户端自动结束。
    d=subprocess.Popen([root+'/bin/ChatClient','127.0.0.1','16140'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT);clients.append(d)
    time.sleep(.2);p.terminate();assert p.wait(timeout=8)==0
    assert d.wait(timeout=3)==0
    output=d.stdout.read().decode();assert '服务器连接已关闭' in output
    print('PASS disconnect while waiting for menu input exits client without pressing Enter')
finally:
    for c in clients:
        if c.poll() is None:c.terminate();c.wait(timeout=3)
    if p.poll() is None:p.terminate();p.wait(timeout=8)
    log.close()
    if uid:sql('DELETE FROM User WHERE id='+str(uid))

# 独立假服务验证客户端的TCP分帧，不涉及数据库。
def mock_test(kind):
    listener=socket.socket();listener.settimeout(5);listener.bind(('127.0.0.1',0));listener.listen(1);port=listener.getsockname()[1]
    errors=[]
    def serve():
        try:
            conn,_=listener.accept()
            with conn:
                conn.settimeout(4);req=json.loads(conn.makefile('rb').readline());assert req['msgid']==1
                if kind=='timeout':time.sleep(11);return
                if kind=='invalid':conn.sendall(b'not-json\n');return
                ack=(json.dumps(dict(msgid=4,errno=0,id=123,name='mock'))+'\n').encode()
                conn.sendall(ack[:8]);time.sleep(.05)
                conn.sendall(ack[8:]+b'{"msgid":13,"msg":"async"}\n')
                time.sleep(.2)
        except Exception as e:errors.append(e)
        finally:listener.close()
    t=threading.Thread(target=serve);t.start()
    result=subprocess.run([root+'/bin/ChatClient','127.0.0.1',str(port)],input='1\n123\ndemo123\n0\n',text=True,capture_output=True,timeout=14)
    t.join();assert not errors,errors
    if kind=='split':assert '登录成功' in result.stdout
    else:assert '请求失败' in result.stdout
    print('PASS client '+kind+' response handling')
for kind in ['split','invalid','timeout']:mock_test(kind)
