"""15.3 isolated group fanout, offline storage snapshot and batched retrieval."""
import argparse,hashlib,json,os,platform,resource,signal,socket,subprocess,time,uuid
from pathlib import Path
from bench_resources import ResourceMonitor,mysql_pid
root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--recipients',type=int,nargs='+',default=[2,5])
p.add_argument('--messages',type=int,default=50)
p.add_argument('--offline-messages',type=int,default=101)
p.add_argument('--bytes',type=int,default=128)
p.add_argument('--counts-file',help='optional JSON map of online case names to request counts')
args=p.parse_args()
if not(all(1<=n<=20 for n in args.recipients) and len(set(args.recipients))==len(args.recipients) and len(args.recipients)<=5 and 1<=args.messages<=1000 and 1<=args.offline_messages<=1000 and 64<=args.bytes<=4096):p.error('recipients: 1..20, at most 5 distinct sizes; messages: 1..1000; bytes: 64..4096')
if resource.getrlimit(resource.RLIMIT_NOFILE)[0]<max(args.recipients)*2+64:p.error('file descriptor limit too low')
case_counts=json.loads(Path(args.counts_file).read_text()) if args.counts_file else {}
allowed={'group-'+str(n)+'-'+kind for n in args.recipients for kind in ('local','mixed')}
if not isinstance(case_counts,dict) or any(k not in allowed or type(v) is not int or not 1<=v<=1000 for k,v in case_counts.items()):p.error('invalid case request counts')
run=uuid.uuid4().hex[:12];work=root/'.runtime/bench'/('group-'+run);work.mkdir(parents=True)
print('Run directory:',work,flush=True)
config='[client]\nuser=chat_module_test\npassword=Module02_test_only!\nhost=127.0.0.1\nprotocol=tcp\n'
def sql(q):return subprocess.run(['mysql','--defaults-file=/dev/stdin','-N','--raw','chat_module_test','-e',q],input=config,text=True,capture_output=True,check=True,timeout=10).stdout.strip()
def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
def wait(check,seconds=10):
    end=time.monotonic()+seconds
    while not check():
        if time.monotonic()>end:raise RuntimeError('wait deadline')
        time.sleep(.02)
def terminate(signum,frame):raise KeyboardInterrupt()
signal.signal(signal.SIGTERM,terminate)
ports=set()
while len(ports)<3:ports.add(port())
a,b,rp=ports;nodeA='bench153-A-'+run;nodeB='bench153-B-'+run
base=dict(os.environ,CHAT_DB_NAME='chat_module_test',CHAT_DB_USER='chat_module_test',CHAT_DB_PASSWORD='Module02_test_only!',CHAT_REDIS_HOST='127.0.0.1',CHAT_REDIS_PORT=str(rp),CHAT_REDIS_PASSWORD='',CHAT_REDIS_PREFIX='bench153:'+run+':')
children=[];logs=[];ids=[];names=[];groupnames=[];rows=[];proc=None;failure=None;active_report=None;active_case=None
metadata=dict(run_id=run,parameters=vars(args),platform=platform.platform(),cpu_count=os.cpu_count(),fd_limit=list(resource.getrlimit(resource.RLIMIT_NOFILE)),ports=dict(a=a,b=b,redis=rp),node_ids=[nodeA,nodeB],io_threads_per_node=4,notes=['same VM and shared test MySQL','two backend processes remain running for both online cases','closed loop: one group request, wait for ACK and all recipients','offline storage and retrieval are separate intervals','no CPU/RSS profiling or capacity claim'])
metadata['case_counts']=case_counts
metadata['binary_sha256']={name:hashlib.sha256((root/'bin'/name).read_bytes()).hexdigest() for name in ('ChatServer','ChatGroupBench')}
metadata['database_pool']=dict(enabled=os.environ.get('CHAT_DB_POOL','1')!='0',size=os.environ.get('CHAT_DB_POOL_SIZE','8'),profiling=bool(os.environ.get('CHAT_DB_PROFILE')))
cache=root/'build/CMakeCache.txt';metadata['cmake_cache']=[s for s in cache.read_text().splitlines() if s.startswith(('CMAKE_BUILD_TYPE:','CMAKE_CXX_FLAGS:'))] if cache.exists() else []
(work/'environment.json').write_text(json.dumps(metadata,indent=2))
def save():
    (work/'results.json').write_text(json.dumps(dict(environment=metadata,runs=rows,error=failure),indent=2))
    lines=['# 15.3 group and offline observations','', 'Group requests, recipient delivery and offline storage are different units.','', '| Case | Recipients | ACKs | Online deliveries | Offline submissions | Request/s | Delivery/s | ok |','| --- | --- | --- | --- | --- | --- | --- | --- |']
    retrieval_lines=[]
    for r in rows:
        m=r.get('measurement',{});n=r.get('config',{}).get('recipients','—')
        lines.append(f"| {r['case']} | {n} | {m.get('ack_ok','—')} | {m.get('received_unique','—')} | {m.get('stored_offline_submitted','—')} | {m.get('request_per_second',0):.2f} | {m.get('delivery_per_second',0):.2f} | {r.get('ok',False)} |")
        if 'retrieval' in r:retrieval_lines.append(f"\nRetrieval {r['case']}: unique={r['retrieval'].get('received_unique',0)}, missing={r['retrieval'].get('missing',0)}, batches={json.dumps(r['retrieval'].get('batches',{}))}\n")
    if retrieval_lines:lines+=['','## Retrieval batches','']+retrieval_lines
    if failure:lines+=['','Aborted: '+failure]
    lines+=['','See per-case JSON for actual owners/routes, database snapshot verification and ACK/delivery/retrieval latency. Results are finite closed-loop samples, not maximum capacity.']
    (work/'summary.md').write_text('\n'.join(lines)+'\n')
def start(name,command,env):
    log=open(work/(name+'.log'),'w');logs.append(log);child=subprocess.Popen(command,env=env,stdout=log,stderr=log);children.append(child);return child
def ready(child,number):
    if child.poll() is not None:raise RuntimeError('test service stopped; inspect log')
    try:
        with socket.create_connection(('127.0.0.1',number),timeout=.1):return True
    except OSError:return False
class Peer:
    def __init__(self):self.s=socket.create_connection(('127.0.0.1',a),timeout=5);self.f=self.s.makefile('rb')
    def request(self,j):self.s.sendall((json.dumps(j)+'\n').encode());return json.loads(self.f.readline())
    def close(self):self.f.close();self.s.close()
def offline(users):return sql('SELECT COUNT(*) FROM User WHERE id IN ('+','.join(map(str,users))+') AND state="online"')=='0'
def run_tool(case,mode,accounts,gid,messages,token,source=13):
    global proc,active_report,active_case
    wait(lambda:offline(ids));assert sql('SELECT COUNT(*) FROM OfflineMessage WHERE userid IN ('+','.join(map(str,ids))+')')=='0' or mode=='offline_fetch'
    report=work/(case+'.json');active_report=report;active_case=case
    cfg=dict(mode=mode,run_id=token,accounts=accounts,group_id=gid,messages=messages,message_bytes=args.bytes,warmup=3,source_msgid=source,timeout_seconds=10,max_seconds=60,ready_file=str(work/(case+'.ready')),start_file=str(work/(case+'.go')),report=str(report))
    path=work/(case+'-config.json')
    with os.fdopen(os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'w') as f:json.dump(cfg,f)
    owners={};routes=[];monitor=None
    try:
        log=open(work/(case+'-client.log'),'w');logs.append(log);clientenv=dict(os.environ);clientenv.pop('CHAT_BENCH_MONITOR',None)
        if os.environ.get('CHAT_BENCH_SAMPLE')=='1':
            roster={'redis':children[0].pid,'server_a':children[1].pid,'server_b':children[2].pid,'mysql_shared':mysql_pid()}
            monitor=ResourceMonitor(work/(case+'-resources.json'),roster);clientenv['CHAT_BENCH_MONITOR']=monitor.socket_path
        proc=subprocess.Popen([str(root/'bin/ChatGroupBench'),str(path)],stdout=log,stderr=log,env=clientenv)
        if mode!='offline_fetch':
            def tool_ready():
                if proc.poll() is not None:raise RuntimeError('C++ preparation failed; inspect report')
                return Path(cfg['ready_file']).exists()
            wait(tool_ready,60)
            observed=json.loads(Path(cfg['ready_file']).read_text())['ids']
            expected=[x['id'] for x in accounts] if mode=='group_online' else [accounts[0]['id']]
            assert observed==expected
            data=sql('SELECT id,state,server_id FROM User WHERE id IN ('+','.join(map(str,ids))+')')
            states={int(line.split('\t')[0]):line.split('\t')[1:] for line in data.splitlines()}
            for user in observed:
                assert states[user][0]=='online';owners[str(user)]=states[user][1]
                expected_owner=nodeA if next(x for x in accounts if x['id']==user)['port']==a else nodeB
                assert states[user][1]==expected_owner
            if mode=='group_online':routes=['local' if owners[str(x['id'])]==owners[str(accounts[0]['id'])] else 'remote' for x in accounts[1:]]
            else:
                assert all(states[x['id']][0]=='offline' for x in accounts[1:]);routes=['offline']*(len(accounts)-1)
            go=Path(cfg['start_file']);temp=go.with_suffix('.tmp');temp.write_text(json.dumps(dict(routes=routes)));temp.replace(go)
        code=proc.wait(timeout=150);proc=None
        resource_summary=monitor.close() if monitor else None;monitor=None
        result=json.loads(report.read_text())
        if resource_summary is not None:
            result['resources']=resource_summary
            if resource_summary['errors']:result['ok']=False;result['resource_error']=resource_summary['errors']
        result.update(case=case,owners=owners);rows.append(result);report.write_text(json.dumps(result,indent=2));save()
        assert code==0 and result['ok'],case+' failed; inspect JSON'
        if mode=='group_online':
            m=result['measurement'];assert m['ack_ok']==messages and m['received_unique']==messages*(len(accounts)-1) and m['missing']==m['duplicate']==m['unknown']==0
            assert result['recipient_routes']==routes
            for route in set(routes):assert m['by_route'][route]['received_unique']==routes.count(route)*messages
        elif mode!='offline_fetch':assert result['measurement']['stored_offline_submitted']==messages*(len(accounts)-1)
        else:
            r=result['retrieval'];assert r['received_unique']==messages*(len(accounts)-1)
            expected_sizes=[100]*(messages//100)+([messages%100] if messages%100 else [])+[0]
            assert all(sizes==expected_sizes for sizes in r['batches'].values()),r
        print('PASS '+case+': '+(str(result['measurement']['received_unique'])+' online deliveries' if mode=='group_online' else str(result.get('retrieval',{}).get('received_unique',result.get('measurement',{}).get('stored_offline_submitted',0)))+' offline records'),flush=True)
        return result
    finally:
        if proc and proc.poll() is None:proc.terminate();proc.wait(timeout=5)
        proc=None
        if monitor:
            resource_summary=monitor.close();monitor=None
            if report.exists():
                partial=json.loads(report.read_text());partial['resources']=resource_summary;report.write_text(json.dumps(partial,indent=2))
        path.unlink(missing_ok=True)
def snapshot(accounts,gid,messages,token,source):
    targets={x['id'] for x in accounts[1:]};total=0
    for uid in ids:
        raw=sql(f'SELECT message FROM OfflineMessage WHERE userid={uid} ORDER BY id')
        records=[json.loads(line) for line in raw.splitlines()] if raw else []
        if uid not in targets:assert not records,'unexpected recipient stored message';continue
        assert len(records)==messages
        expected=set()
        for seq in range(messages):
            prefix=token+':store:'+str(seq)+'|';expected.add(prefix+'x'*(args.bytes-len(prefix)))
        observed=[]
        for msg in records:
            assert msg['msgid']==source and msg['id']==accounts[0]['id'] and msg['toid']==uid
            if source==13:assert msg['groupid']==gid
            observed.append(msg['msg'])
        assert len(set(observed))==messages and set(observed)==expected
        total+=len(records)
    return dict(ok=True,expected=len(targets)*messages,rows=total,payload_and_recipient_verified=True)
try:
    redis=start('redis',['redis-server','--bind','127.0.0.1','--port',str(rp),'--save','','--appendonly','no','--dir',str(work)],base);wait(lambda:ready(redis,rp))
    for name,number,node in [('a',a,nodeA),('b',b,nodeB)]:
        child=start('node-'+name,[str(root/'bin/ChatServer'),str(number)],dict(base,CHAT_SERVER_ID=node));wait(lambda:ready(child,number))
    for size in args.recipients:
        current=[]
        for i in range(size+1):
            name='b153_'+run+'_'+str(size)+'_'+str(i);names.append(name);peer=Peer()
            try:
                reply=peer.request(dict(msgid=2,name=name,password='demo123'));assert reply['errno']==0;current.append(reply['id']);ids.append(reply['id'])
            finally:peer.close()
        gid=None;groupname='b153_'+run+'_'+str(size);groupnames.append(groupname)
        for i,uid in enumerate(current):
            wait(lambda:offline([uid]));peer=Peer()
            try:
                assert peer.request(dict(msgid=1,id=uid,password='demo123'))['errno']==0
                if i==0:
                    reply=peer.request(dict(msgid=9,groupname=groupname,groupdesc='15.3 isolated benchmark'));assert reply['errno']==0;gid=reply['groupid']
                else:assert peer.request(dict(msgid=11,groupid=gid))['errno']==0
            finally:peer.close()
        members=set(map(int,sql(f'SELECT userid FROM GroupUser WHERE groupid={gid}').splitlines()));assert members==set(current)
        accounts=[dict(id=uid,password='demo123',port=a) for uid in current]
        for mixed in (False,True):
            online=[dict(x,port=b if mixed and i%2 else a) for i,x in enumerate(accounts)]
            case=f'group-{size}-'+('mixed' if mixed else 'local')
            run_tool(case,'group_online',online,gid,case_counts.get(case,args.messages),run)
        for single in (True,False):
            chosen=accounts[:2] if single else accounts;tag=f'offline-{size}-'+('single' if single else 'group');token=uuid.uuid4().hex[:12]
            stored=run_tool(tag+'-store','offline_single_store' if single else 'offline_group_store',chosen,gid,args.offline_messages,token)
            check=snapshot(chosen,gid,args.offline_messages,token,5 if single else 13);stored['database_snapshot']=check;(work/(tag+'-store.json')).write_text(json.dumps(stored,indent=2));save()
            fetched=run_tool(tag+'-fetch','offline_fetch',chosen,gid,args.offline_messages,token,5 if single else 13)
            wait(lambda:offline(ids));remaining=int(sql('SELECT COUNT(*) FROM OfflineMessage WHERE userid IN ('+','.join(map(str,ids))+')'));assert remaining==0
            fetched['database_remaining']=remaining;(work/(tag+'-fetch.json')).write_text(json.dumps(fetched,indent=2));save()
except BaseException as e:
    failure=f'{type(e).__name__}: {e}'
    if active_report and active_report.exists() and not any(r['case']==active_case for r in rows):
        result=json.loads(active_report.read_text());result['case']=active_case;rows.append(result)
    raise
finally:
    errors=[]
    for child in reversed(children):
        try:
            if child.poll() is None:child.terminate();child.wait(timeout=10)
        except Exception as e:errors.append('child cleanup: '+str(e))
    try:
        if groupnames:sql('DELETE FROM AllGroup WHERE groupname IN ('+','.join("'"+n+"'" for n in groupnames)+')')
        if names:sql('DELETE FROM User WHERE name IN ('+','.join("'"+n+"'" for n in names)+')')
    except Exception as e:errors.append('database cleanup: '+str(e))
    for log in logs:log.close()
    metadata['cleanup_errors']=errors;original=failure
    if errors:failure=(failure or '')+' cleanup errors: '+'; '.join(errors)
    save();print('Reports:',work,flush=True)
    if errors and original is None:raise RuntimeError(failure)
