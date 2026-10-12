"""Controlled fixtures verify group counts and offline batch corruption detection."""
import json,os,socket,subprocess,threading,time,uuid
from pathlib import Path
root=Path(__file__).resolve().parents[1];work=root/'.runtime/bench'/('group-protocol-'+uuid.uuid4().hex[:12]);work.mkdir(parents=True)
for mode in ('framing','duplicate','wrong-ack','wrong-recipient','timeout','fetch-duplicate','fetch-wrong-group'):
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(5);port=listener.getsockname()[1]
    streams=[];sockets=[];errors=[];fetch=mode.startswith('fetch');token='protocol153'
    def payload(seq):
        prefix=token+(':store:' if fetch else ':measure:')+str(seq)+'|'
        return dict(msgid=13,id=101,toid=102,groupid=7,msg=prefix+'x'*(128-len(prefix)))
    def accept():
        s,_=listener.accept();s.settimeout(3);sockets.append(s);f=s.makefile('rb');streams.append(f);login=json.loads(f.readline());assert login['msgid']==1;return s,f
    def fixture():
        try:
            if fetch:
                batches=[[payload(i) for i in range(100)],[payload(0)],[]] if mode=='fetch-duplicate' else [[dict(payload(0),groupid=8)],[]]
                for batch in batches:
                    s,f=accept();s.sendall((json.dumps(dict(msgid=4,errno=0,offlinemsg=batch,offline_batch_limit=100))+'\n').encode())
                return
            for _ in range(2):
                s,f=accept();s.sendall(b'{"msgid":4,"errno":0,"offlinemsg":[]}\n')
            end=time.monotonic()+2
            while not (work/(mode+'.ready')).exists():assert time.monotonic()<end;time.sleep(.01)
            go=work/(mode+'.go');tmp=go.with_suffix('.tmp');tmp.write_text(json.dumps(dict(routes=['local'])));tmp.replace(go)
            req=json.loads(streams[0].readline());assert req['msgid']==13
            ack=dict(msgid=14,errno=0,submitted=1,submitted_local=1,submitted_remote=0,stored_offline=0,failed=0)
            if mode=='wrong-ack':ack['submitted_local']=2
            sockets[0].sendall((json.dumps(ack)+'\n').encode())
            if mode in ('wrong-ack','timeout'):time.sleep(.4);return
            msg=payload(0)
            if mode=='wrong-recipient':msg['toid']=999
            raw=(json.dumps(msg)+'\n').encode()
            if mode=='duplicate':raw+=raw
            sockets[1].sendall(raw[:9]);time.sleep(.01);sockets[1].sendall(raw[9:]);time.sleep(.4)
        except Exception as e:errors.append(str(e))
        finally:
            for f in streams:f.close()
            for s in sockets:s.close()
            listener.close()
    cfg=dict(mode='offline_fetch' if fetch else 'group_online',run_id=token,accounts=[dict(id=i,password='x',port=port) for i in (101,102)],messages=101 if mode=='fetch-duplicate' else 1,group_id=7,warmup=0,message_bytes=128,timeout_seconds=.25,max_seconds=3,ready_file=str(work/(mode+'.ready')),start_file=str(work/(mode+'.go')),report=str(work/(mode+'.json')))
    path=work/(mode+'-config.json')
    with os.fdopen(os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'w') as f:json.dump(cfg,f)
    thread=threading.Thread(target=fixture,daemon=True);thread.start()
    try:r=subprocess.run([str(root/'bin/ChatGroupBench'),str(path)],capture_output=True,text=True,timeout=8)
    finally:path.unlink()
    thread.join(timeout=5);assert not thread.is_alive();assert not errors,errors
    report=json.loads((work/(mode+'.json')).read_text());assert (r.returncode==0)==(mode=='framing'),report
    part=report['retrieval' if fetch else 'measurement']
    if mode in ('duplicate','fetch-duplicate'):assert part['duplicate']==1,report
    if mode in ('wrong-recipient','fetch-wrong-group'):assert part['unknown']==1 and part['missing']==1,report
    if mode=='wrong-ack':assert 'route/count mismatch' in part['error'],report
    if mode=='timeout':assert part['missing']==1 and 'timeout' in part['error'],report
    print('PASS controlled '+mode,flush=True)
print('Reports:',work)
