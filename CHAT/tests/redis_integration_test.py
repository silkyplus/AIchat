"""Runs C++ Redis tests on a private Redis instance; leaves system Redis untouched."""
import os, socket, subprocess, tempfile, time, select, uuid
from pathlib import Path
root=Path(__file__).resolve().parents[1]

def free_port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

def run(args,env=None,ok=True):
    result=subprocess.run(args,env=env,text=True,capture_output=True,timeout=15)
    assert (result.returncode==0)==ok,(args,result.stdout,result.stderr)
    return result.stdout+result.stderr

def wait_output(p, token):
    output=b'';end=time.monotonic()+5
    while token.encode() not in output:
        assert time.monotonic()<end,output
        if select.select([p.stdout],[],[],.1)[0]:
            chunk=os.read(p.stdout.fileno(),8192);assert chunk,output;output+=chunk
    return output.decode()

with tempfile.TemporaryDirectory(prefix='chat-redis-test-') as work:
    server=None;sub=None;log=open(Path(work)/'redis.log','w+')
    try:
        port=free_port();env=dict(os.environ,CHAT_REDIS_HOST='127.0.0.1',CHAT_REDIS_PORT=str(port),CHAT_REDIS_PASSWORD='')
        server=subprocess.Popen(['redis-server','--bind','127.0.0.1','--port',str(port),'--save','','--appendonly','no','--dir',work],stdout=log,stderr=log)
        end=time.monotonic()+5
        while True:
            assert server.poll() is None,'private Redis exited'
            ping=subprocess.run(['redis-cli','-p',str(port),'ping'],text=True,capture_output=True)
            if ping.returncode==0 and ping.stdout.strip()=='PONG':break
            assert time.monotonic()<end;time.sleep(.05)
        print(run([str(root/'bin/RedisPubSubTest'),'127.0.0.1',str(port)],env).strip(),flush=True)
        channel='test:demo:'+uuid.uuid4().hex
        sub=subprocess.Popen([str(root/'bin/RedisDemo'),'subscribe',channel],env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        wait_output(sub,'订阅成功')
        output=run([str(root/'bin/RedisDemo'),'publish',channel,'两个C++进程 中文 100%'],env)
        assert '订阅连接数: 1' in output,output
        wait_output(sub,'两个C++进程 中文 100%')
        sub.stdin.write(b'\n');sub.stdin.flush();assert sub.wait(timeout=3)==0;sub=None
        assert '订阅连接数: 0' in run([str(root/'bin/RedisDemo'),'publish',channel,'after quit'],env)
        print('PASS independent C++ demo processes, Chinese/percent payload and Enter unsubscribe',flush=True)
        sub=subprocess.Popen([str(root/'bin/RedisDemo'),'subscribe',channel],env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        wait_output(sub,'订阅成功')
        server.terminate();assert server.wait(timeout=5)==0;server=None
        assert sub.wait(timeout=3)==1
        assert '连接已断开' in sub.stdout.read().decode();sub=None
        assert 'connection/authentication failed' in run([str(root/'bin/RedisDemo'),'publish',channel,'offline'],env,False)
        print('PASS Redis termination wakes receiver, bounded shutdown and unavailable Redis error',flush=True)
        # AUTH 成功/失败使用另一独立实例，密码只用于这个临时测试。
        port=free_port();env.update(CHAT_REDIS_PORT=str(port),CHAT_REDIS_PASSWORD='Module131_test_only!')
        server=subprocess.Popen(['redis-server','--bind','127.0.0.1','--port',str(port),'--save','','--appendonly','no','--requirepass',env['CHAT_REDIS_PASSWORD'],'--dir',work],stdout=log,stderr=log)
        time.sleep(.2);assert server.poll() is None
        print(run([str(root/'bin/RedisPubSubTest'),'127.0.0.1',str(port)],env).strip(),flush=True)
        for password in ('','wrong'):
            wrong=dict(env,CHAT_REDIS_PASSWORD=password)
            assert 'connection/authentication failed' in run([str(root/'bin/RedisDemo'),'publish',channel,'auth'],wrong,False)
        print('PASS password authentication and missing/wrong password rejection',flush=True)
    finally:
        if sub and sub.poll() is None:sub.terminate();sub.wait(timeout=3)
        if server and server.poll() is None:server.terminate();server.wait(timeout=5)
        log.close()
