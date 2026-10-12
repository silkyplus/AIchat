"""15.2 isolated single-chat topology baseline. ChatBench provides the C++ load."""
import argparse,json,os,platform,resource,signal,socket,subprocess,time,uuid,statistics,hashlib,re
from pathlib import Path
from bench_resources import ResourceMonitor,mysql_pid
root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--pairs',type=int,default=10)
parser.add_argument('--messages',type=int,default=100)
parser.add_argument('--bytes',type=int,default=128)
parser.add_argument('--repeats',type=int,default=3)
parser.add_argument('--topologies',nargs='+',choices=['single_direct','redis_direct','nginx_entry'],default=['single_direct','redis_direct','nginx_entry'])
args=parser.parse_args()
if not(1<=args.pairs<=100 and 1<=args.messages<=10000 and args.pairs*args.messages<=200000 and 64<=args.bytes<=4096 and 1<=args.repeats<=10):parser.error('limits: pairs 1..100, messages 1..10000, samples <=200000, bytes 64..4096, repeats 1..10')
if resource.getrlimit(resource.RLIMIT_NOFILE)[0]<4*args.pairs+64:parser.error('file descriptor limit too small for selected connection count')
run=uuid.uuid4().hex[:12];work=root/'.runtime/bench'/('baseline-'+run);work.mkdir(parents=True)
config='[client]\nuser=chat_module_test\npassword=Module02_test_only!\nhost=127.0.0.1\nprotocol=tcp\n'
def sql(q):return subprocess.run(['mysql','--defaults-file=/dev/stdin','-N','chat_module_test','-e',q],input=config,text=True,capture_output=True,check=True,timeout=10).stdout.strip()
def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
def wait(check,seconds=10):
    end=time.monotonic()+seconds
    while not check():
        if time.monotonic()>end:raise RuntimeError('wait deadline')
        time.sleep(.02)
def ready(p,number):
    if p.poll() is not None:raise RuntimeError('service exited; inspect logs')
    try:
        with socket.create_connection(('127.0.0.1',number),timeout=.1):return True
    except OSError:return False
# Ctrl+C and SIGTERM both run finally cleanup; only this runner's child processes are signalled.
def terminate(signum,frame):raise KeyboardInterrupt()
signal.signal(signal.SIGTERM,terminate)
ports=set()
while len(ports)<4:ports.add(port())
a,b,entry,rp=ports;nodeA='bench152-A-'+run;nodeB='bench152-B-'+run
base=dict(os.environ,CHAT_DB_NAME='chat_module_test',CHAT_DB_USER='chat_module_test',CHAT_DB_PASSWORD='Module02_test_only!',CHAT_REDIS_HOST='127.0.0.1',CHAT_REDIS_PORT=str(rp),CHAT_REDIS_PASSWORD='',CHAT_REDIS_PREFIX='bench152:'+run+':')
proxyenv=dict(os.environ,CHAT_NGINX_RUN_DIR=str(work/'nginx'),CHAT_NGINX_PORT=str(entry),CHAT_NGINX_BACKEND_A_PORT=str(a),CHAT_NGINX_BACKEND_B_PORT=str(b),CHAT_NGINX_STRATEGY='round_robin')
servers=[];logs=[];ids=[];redis=None;benchproc=None;monitor=None;proxy_started=False;rows=[];failure=None
metadata=dict(platform=platform.platform(),cpu_count=os.cpu_count(),fd_limit=list(resource.getrlimit(resource.RLIMIT_NOFILE)),parameters=vars(args),load_model='closed loop; ACK and delivery before next request; same VM',io_threads_per_node=4,nginx_workers=1,run_id=run,notes=['shared system MySQL/test database','no CPU/RSS profiling in 15.2','debug/default build unless explicitly configured; see cmake_cache excerpt'])
metadata['database_pool']=dict(enabled=os.environ.get('CHAT_DB_POOL','1')!='0',size=os.environ.get('CHAT_DB_POOL_SIZE','8'),profiling=bool(os.environ.get('CHAT_DB_PROFILE')))
cache=root/'build/CMakeCache.txt'
metadata['ports']=dict(node_a=a,node_b=b,nginx=entry,redis=rp)
metadata['node_ids']=[nodeA,nodeB]
metadata['binary_sha256']={name:hashlib.sha256((root/'bin'/name).read_bytes()).hexdigest() for name in ('ChatServer','ChatBench')}
metadata['cmake_cache']=[line for line in cache.read_text().splitlines() if line.startswith(('CMAKE_BUILD_TYPE:','CMAKE_CXX_FLAGS:','CMAKE_CXX_COMPILER:'))] if cache.exists() else []
(work/'environment.json').write_text(json.dumps(metadata,indent=2))
print('Run directory:',work,flush=True)
def start(name,command,env):
    log=open(work/(name+'.log'),'w');logs.append(log)
    return subprocess.Popen(command,env=env,stdout=log,stderr=log)
def proxy(action):
    subprocess.run([str(root/'deploy/nginx/chat-nginx.sh'),action],env=proxyenv,capture_output=True,text=True,check=True,timeout=10)
def offline():
    return not ids or sql('SELECT COUNT(*) FROM User WHERE id IN ('+','.join(map(str,ids))+') AND state="online"')=='0'
def save_summary():
    (work/'results.json').write_text(json.dumps(dict(environment=metadata,runs=rows,error=failure),indent=2))
    lines=['# 15.2 single-chat baseline','', 'Closed-loop client observations on one VM; not a capacity limit or scaling proof. Failed runs are retained.','', '| Topology | Repeat | Pairs local/remote | Delivered/expected | Delivery/s | ACK P95 ms | Delivery P95 ms | ok |','| --- | --- | --- | --- | --- | --- | --- | --- |']
    for r in rows:
        m=r.get('measurement',{});routes=r.get('pair_routes',[])
        lines.append(f"| {r['topology']} | {r['repeat']} | {routes.count('local')}/{routes.count('remote')} | {m.get('received_unique',0)}/{m.get('expected',0)} | {m.get('delivery_per_second',0):.2f} | {m.get('ack_latency',{}).get('p95_ms',0):.3f} | {m.get('delivery_latency',{}).get('p95_ms',0):.3f} | {r.get('ok',False)} |")
    lines+=['','## Repeat summary','', '| Topology | Successful/all runs | Median delivery/s | Min/max delivery/s |','| --- | --- | --- | --- |']
    for topology in ('single_direct','redis_direct','nginx_entry'):
        group=[r for r in rows if r['topology']==topology];values=[r['measurement']['delivery_per_second'] for r in group if r.get('ok')]
        lines.append(f"| {topology} | {len(values)}/{len(group)} | {statistics.median(values):.2f} | {min(values):.2f}/{max(values):.2f} |" if values else f'| {topology} | 0/{len(group)} | — | — |')
    if failure:lines+=['','Run aborted: '+failure]
    lines+=['','Per-route latency/throughput and observed DB ownership are in each run JSON. Route throughput uses the whole run duration. Medians above include successful runs only; failures remain visible. Registration/login are outside chat measurement.']
    (work/'summary.md').write_text('\n'.join(lines)+'\n')
try:
    redis=start('redis',['redis-server','--bind','127.0.0.1','--port',str(rp),'--save','','--appendonly','no','--dir',str(work)],base);wait(lambda:ready(redis,rp))
    pa=start('node-a',[str(root/'bin/ChatServer'),str(a)],dict(base,CHAT_SERVER_ID=nodeA));servers.append(pa);wait(lambda:ready(pa,a))
    for i in range(args.pairs*2):
        with socket.create_connection(('127.0.0.1',a),timeout=5) as s:
            s.sendall((json.dumps(dict(msgid=2,name='b152_'+run+'_'+str(i),password='demo123'))+'\n').encode())
            with s.makefile('rb') as f:reply=json.loads(f.readline())
            if reply.get('errno')!=0:raise RuntimeError('test registration failed')
            ids.append(reply['id'])
    for topology in args.topologies:
        if topology in ('redis_direct','nginx_entry') and len(servers)==1:
            pb=start('node-b',[str(root/'bin/ChatServer'),str(b)],dict(base,CHAT_SERVER_ID=nodeB));servers.append(pb);wait(lambda:ready(pb,b))
        if topology=='nginx_entry':proxy('start');proxy_started=True
        for repeat in range(1,args.repeats+1):
            wait(offline)
            name=f'{topology}-{repeat}';readyfile=work/(name+'.ready');gofile=work/(name+'.go');report=work/(name+'.json')
            accounts=[dict(id=uid,password='demo123',port=(entry if topology=='nginx_entry' else b if topology=='redis_direct' and i%2 else a)) for i,uid in enumerate(ids)]
            order=list(range(len(ids)))
            if topology=='nginx_entry':
                # One remote pair followed by groups of two potentially local pairs.
                # Observed server_id, not this intended pattern, determines classification.
                order=[0,1]
                for pair in range(1,args.pairs,2):
                    order.extend([2*pair,2*(pair+1),2*pair+1,2*(pair+1)+1] if pair+1<args.pairs else [2*pair,2*pair+1])
            cfg=dict(host='127.0.0.1',port=a,run_id=run,accounts=accounts,connection_order=order,messages_per_pair=args.messages,message_bytes=args.bytes,warmup_per_pair=5,timeout_seconds=10,max_seconds=60,ready_file=str(readyfile),start_file=str(gofile),report=str(report))
            path=work/(name+'-config.json')
            with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as f:json.dump(cfg,f)
            try:
                log=open(work/(name+'-client.log'),'w');logs.append(log)
                clientenv=dict(os.environ);clientenv.pop('CHAT_BENCH_MONITOR',None)
                if os.environ.get('CHAT_BENCH_SAMPLE')=='1':
                    roster={'redis':redis.pid,'mysql_shared':mysql_pid()};roster.update({'server_'+str(i):p.pid for i,p in enumerate(servers)})
                    monitor=ResourceMonitor(work/(name+'-resources.json'),roster,work/'nginx/nginx.pid' if proxy_started else None)
                    clientenv['CHAT_BENCH_MONITOR']=monitor.socket_path
                benchproc=subprocess.Popen([str(root/'bin/ChatBench'),str(path)],stdout=log,stderr=log,env=clientenv)
                def bench_ready():
                    if benchproc.poll() is not None:raise RuntimeError('ChatBench exited before ownership check; inspect run report')
                    return readyfile.exists() and readyfile.stat().st_size>0
                wait(bench_ready,60)
                observed=json.loads(readyfile.read_text());assert observed['ids']==ids
                data=sql('SELECT id,state,server_id FROM User WHERE id IN ('+','.join(map(str,ids))+')')
                owners={int(line.split('\t')[0]):line.split('\t')[2] for line in data.splitlines() if line.split('\t')[1]=='online'}
                assert set(owners)==set(ids),'some users are not online'
                assert all(owner in (nodeA,nodeB) for owner in owners.values()),'unexpected node'
                routes=['local' if owners[ids[2*p]]==owners[ids[2*p+1]] else 'remote' for p in range(args.pairs)]
                assert topology!='single_direct' or all(owners[u]==nodeA for u in ids)
                assert topology!='redis_direct' or all(owners[u]==(nodeA if i%2==0 else nodeB) for i,u in enumerate(ids))
                if topology=='nginx_entry' and args.pairs>=3:assert set(routes)=={'local','remote'},'entry fixture did not produce mixed routes'
                temp=gofile.with_suffix('.tmp');temp.write_text(json.dumps(dict(routes=routes)));temp.replace(gofile)
                code=benchproc.wait(timeout=140);benchproc=None
                resource_summary=monitor.close() if monitor else None;monitor=None
                result=json.loads(report.read_text())
                if resource_summary is not None:
                    result['resources']=resource_summary
                    if resource_summary['errors']:result['ok']=False;result['resource_error']=resource_summary['errors']
                result.update(topology=topology,repeat=repeat,owners={str(k):v for k,v in owners.items()},node_count=1 if topology=='single_direct' else 2)
                report.write_text(json.dumps(result,indent=2));rows.append(result);save_summary()
                assert code==0 and result['ok'],f'{name}: C++ test failed; inspect report'
                m=result['measurement'];assert m['expected']==m['received_unique']==m['ack_ok']==args.pairs*args.messages
                assert len(result['pair_routes'])==args.pairs and result['pair_routes']==routes
                for route in set(routes):assert m['by_route'][route]['received_unique']==routes.count(route)*args.messages
                print(f"PASS {name}: local/remote={routes.count('local')}/{routes.count('remote')}, received={m['received_unique']}, delivery/s={m['delivery_per_second']:.2f}, P95={m['delivery_latency']['p95_ms']:.3f}ms",flush=True)
            finally:
                if benchproc and benchproc.poll() is None:benchproc.terminate();benchproc.wait(timeout=5)
                benchproc=None
                if monitor:
                    resource_summary=monitor.close();monitor=None
                    if report.exists():
                        partial=json.loads(report.read_text());partial['resources']=resource_summary;report.write_text(json.dumps(partial,indent=2))
                path.unlink(missing_ok=True)
except BaseException as e:
    failure=f'{type(e).__name__}: {e}'
    # Preserve the C++ partial report even if preparation/barrier failed before normal collection.
    for report in work.glob('*.json'):
        match=re.fullmatch(r'(single_direct|redis_direct|nginx_entry)-(\d+)',report.stem)
        if match and not any(r.get('topology')+'-'+str(r.get('repeat'))==report.stem for r in rows):
            r=json.loads(report.read_text());r.update(topology=match.group(1),repeat=int(match.group(2)));rows.append(r)
    raise
finally:
    cleanup_errors=[]
    if proxy_started:
        try:proxy('stop')
        except Exception as e:cleanup_errors.append('Nginx stop: '+str(e))
    for p in list(reversed(servers))+([redis] if redis else []):
        try:
            if p.poll() is None:p.terminate();p.wait(timeout=10)
        except Exception as e:cleanup_errors.append('child stop: '+str(e))
    try:
        # Include attempted names: an interrupted registration may succeed before its ID is read.
        names=["'b152_"+run+'_'+str(i)+"'" for i in range(args.pairs*2)]
        sql('DELETE FROM User WHERE name IN ('+','.join(names)+')')
    except Exception as e:cleanup_errors.append('account cleanup: '+str(e))
    for log in logs:log.close()
    metadata['cleanup_errors']=cleanup_errors
    original_failure=failure
    if cleanup_errors:failure=(failure or '')+' cleanup errors: '+'; '.join(cleanup_errors)
    save_summary();print('Reports:',work,flush=True)
    if cleanup_errors and original_failure is None:raise RuntimeError(failure)
