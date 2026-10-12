"""15.1: isolated real server smoke and controlled protocol failures; business load is C++."""
import json, os, socket, subprocess, time, uuid, threading
from pathlib import Path
root=Path(__file__).resolve().parents[1]
run=uuid.uuid4().hex[:12];work=root/'.runtime/bench'/run;work.mkdir(parents=True)
config='[client]\nuser=chat_module_test\npassword=Module02_test_only!\nhost=127.0.0.1\nprotocol=tcp\n'
def sql(q):return subprocess.run(['mysql','--defaults-file=/dev/stdin','-N','chat_module_test','-e',q],input=config,text=True,capture_output=True,check=True).stdout.strip()
def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
def ready(p,number):
    end=time.monotonic()+8
    while time.monotonic()<end:
        assert p.poll() is None,'process stopped during startup'
        try:
            with socket.create_connection(('127.0.0.1',number),timeout=.1):return
        except OSError:time.sleep(.05)
    raise AssertionError('startup timeout')
def bench(name,number,accounts,ok=True,**extra):
    cfg=dict(host='127.0.0.1',port=number,run_id=run,accounts=accounts,messages_per_pair=20,message_bytes=128,warmup_per_pair=2,timeout_seconds=2,max_seconds=10,report=str(work/(name+'.json')));cfg.update(extra)
    path=work/(name+'-config.json')
    with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as f:f.write(json.dumps(cfg))
    try:r=subprocess.run([str(root/'bin/ChatBench'),str(path)],capture_output=True,text=True,timeout=30)
    finally:path.unlink() # Remove temporary account credentials even on failure.
    result=json.loads((work/(name+'.json')).read_text());assert (r.returncode==0)==ok,(r.stdout,r.stderr)
    assert result['ok']==ok,result
    return result
ids=[];server=redis=None;handles=[]
try:
    rp=port();sp=port()
    for name,args in [('redis',['redis-server','--bind','127.0.0.1','--port',str(rp),'--save','','--appendonly','no','--dir',str(work)]),('server',[str(root/'bin/ChatServer'),str(sp)])]:
        log=open(work/(name+'.log'),'w');handles.append(log)
        env=dict(os.environ,CHAT_DB_NAME='chat_module_test',CHAT_DB_USER='chat_module_test',CHAT_DB_PASSWORD='Module02_test_only!',CHAT_REDIS_HOST='127.0.0.1',CHAT_REDIS_PORT=str(rp),CHAT_REDIS_PASSWORD='',CHAT_REDIS_PREFIX='bench151:'+run+':',CHAT_SERVER_ID='bench151-'+run)
        p=subprocess.Popen(args,env=env,stdout=log,stderr=log)
        if name=='redis':redis=p
        else:server=p
        ready(p,rp if name=='redis' else sp)
    for i in range(4):
        with socket.create_connection(('127.0.0.1',sp),timeout=3) as s:
            s.sendall((json.dumps(dict(msgid=2,name='bench_'+run+'_'+str(i),password='demo123'))+'\n').encode());reply=json.loads(s.makefile('rb').readline());assert reply['errno']==0;ids.append(reply['id'])
    accounts=[dict(id=i,password='demo123') for i in ids]
    result=bench('real-server',sp,accounts)
    m=result['measurement'];assert m['sent']==m['ack_ok']==m['received_unique']==40 and m['missing']==m['unknown']==m['duplicate']==0
    assert result['connections_ok']==result['login_ok']==4
    print('PASS real C++ server: 2 pairs, warmup excluded, 40 ACKs and 40 unique deliveries',flush=True)
    # Wait for disconnect cleanup before reusing users.
    end=time.monotonic()+5
    while sql('SELECT COUNT(*) FROM User WHERE id IN ('+','.join(map(str,ids))+') AND state="online"')!='0':
        assert time.monotonic()<end;time.sleep(.05)
    wrong=[dict(id=i,password='wrong') for i in ids]
    assert bench('login-rejected',sp,wrong,False)['login_ok']==0
    assert bench('connection-refused',port(),accounts,False)['connections_ok']==0
    print('PASS wrong password and unavailable endpoint produce bounded failure reports',flush=True)
finally:
    for p in (server,redis):
        if p and p.poll() is None:p.terminate();p.wait(timeout=8)
    if ids:sql('DELETE FROM User WHERE id IN ('+','.join(map(str,ids))+')')
    for h in handles:h.close()

# A controlled fixture checks parser/statistics, not performance. No external resources.
for mode in ('framing','duplicate','timeout','disconnect','ack-failed','unknown','route-mismatch'):
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(5);number=listener.getsockname()[1];sockets=[];streams=[];errors=[]
    def fixture():
        try:
            for _ in range(2):
                s,_=listener.accept();s.settimeout(4);sockets.append(s);f=s.makefile('rb');streams.append(f);login=json.loads(f.readline());payload=b'{"msgid":4,"errno":0}\n';s.sendall(payload[:7]);s.sendall(payload[7:])
            if mode=='route-mismatch':
                end=time.monotonic()+2
                while not (work/'route.ready').exists():
                    assert time.monotonic()<end;time.sleep(.01)
                target=work/'route.go';temp=work/'route.tmp';temp.write_text(json.dumps(dict(routes=['remote'])));temp.replace(target)
            f=sockets[0].makefile('rb');streams.append(f);req=json.loads(f.readline())
            if mode=='disconnect':sockets[0].shutdown(socket.SHUT_RDWR);return
            ack={'msgid':6,'errno':1 if mode=='ack-failed' else 0,'status':'submitted'};sockets[0].sendall((json.dumps(ack)+'\n').encode())
            if mode in ('timeout','ack-failed','route-mismatch'):time.sleep(.7);return
            msg=dict(msgid=5,id=101,toid=102,msg=req['msg'])
            if mode=='unknown':msg['msg']='unrecognized'
            payload=(json.dumps(msg)+'\n').encode()
            if mode=='duplicate':payload+=payload
            sockets[1].sendall(payload[:11]);time.sleep(.01);sockets[1].sendall(payload[11:]);time.sleep(.7)
        except Exception as e:errors.append(str(e))
        finally:
            for f in streams:f.close()
            for s in sockets:s.close()
            listener.close()
    thread=threading.Thread(target=fixture,daemon=True);thread.start()
    extra=dict(ready_file=str(work/'route.ready'),start_file=str(work/'route.go')) if mode=='route-mismatch' else {}
    r=bench('fixture-'+mode,number,[dict(id=101,password='x'),dict(id=102,password='x')],mode=='framing',warmup_per_pair=0,messages_per_pair=1,timeout_seconds=.3,max_seconds=2,**extra)
    thread.join(timeout=5);assert not thread.is_alive();assert not errors,errors
    if mode=='route-mismatch':assert 'ACK route differs' in r['measurement']['error']
    if mode=='duplicate':assert r['measurement']['duplicate']==1
    if mode=='timeout':assert r['measurement']['timed_out'] and r['measurement']['missing']==1
    if mode=='ack-failed':assert r['measurement']['ack_failed']==1
    if mode=='unknown':assert r['measurement']['unknown']==1 and r['measurement']['missing']==1
    print('PASS controlled '+mode,flush=True)
rows=[]
for report in sorted(work.glob('*.json')):
    data=json.loads(report.read_text());m=data.get('measurement',{})
    rows.append('| '+report.name+' | '+str(data['ok'])+' | '+str(m.get('received_unique','—'))+' | '+str(m.get('missing','—'))+' |')
(work/'summary.md').write_text('# 15.1 correctness smoke results\n\nThese small runs validate the tool, not a performance baseline.\n\n| Report | ok | Received | Missing |\n| --- | --- | --- | --- |\n'+'\n'.join(rows)+'\n')
print('Reports:',work)
