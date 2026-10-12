"""15.4 serial quick/full experiments, repeated summaries and conservative analysis."""
import argparse,json,math,os,platform,signal,statistics,subprocess,time,uuid,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--report-only',help='regenerate summaries from an existing suite directory without running experiments')
p.add_argument('--profile',choices=['quick','full'],default='quick')
p.add_argument('--scenarios',nargs='+',choices=['single','group'],default=['single','group'])
p.add_argument('--repeats',type=int,default=3)
p.add_argument('--bytes',type=int,default=128)
p.add_argument('--pairs',type=int,default=10)
p.add_argument('--recipients',type=int,nargs='+',default=[2,5])
p.add_argument('--overhead-check',action='store_true',help='extra small single-direct runs with sampling off/on; not formal repeats')
args=p.parse_args()
if not(3<=args.repeats<=5 and 64<=args.bytes<=4096 and 1<=args.pairs<=100 and 1<=len(args.recipients)<=5 and len(set(args.recipients))==len(args.recipients) and all(1<=n<=20 for n in args.recipients)):p.error('repeats 3..5, bytes 64..4096, pairs 1..100, recipients 1..20 (at most 5 distinct values)')
if args.report_only:
    work=Path(args.report_only).resolve();saved=json.loads((work/'results.json').read_text());metadata=saved['environment'];jobs=saved['jobs'];failure=saved.get('error')
else:
    work=root/'.runtime/bench'/('suite-'+uuid.uuid4().hex[:12]);work.mkdir(parents=True)
    print('Suite directory:',work,flush=True)
    metadata=dict(parameters=vars(args),platform=platform.platform(),cpu_count=os.cpu_count(),sample_interval_seconds=.2,cpu_unit='100% = one logical CPU',same_vm=True,pilot_target_seconds=5)
    (work/'environment.json').write_text(json.dumps(metadata,indent=2))
    jobs=[];failure=None
active=None;active_record=None

def collect(record):
    log=Path(record['log'])
    if not log.exists():return
    for line in log.read_text().splitlines():
        if line.startswith('Run directory:'):
            directory=Path(line.split(':',1)[1].strip());record['report_directory']=str(directory)
            report=directory/'results.json'
            if report.exists():record['result']=json.loads(report.read_text())
            return

def observations():
    rows=[]
    for job in jobs:
        for r in job.get('result',{}).get('runs',[]):
            m=r.get('measurement');fetch=r.get('retrieval');part=m or fetch or {};mode=r.get('mode','single_online')
            case=r.get('case',r.get('topology','unknown'));phase='retrieval' if fetch is not None else 'store' if mode.startswith('offline_') else 'measure'
            resources=r.get('resources',{});usage=resources.get('phases',{}).get(phase,{})
            cfg=r.get('config',{});routes=r.get('recipient_routes',r.get('pair_routes',[]))
            if fetch is not None:unit='retrieved_records/s';rate=part.get('received_unique',0)/part['seconds'] if part.get('seconds',0)>0 else 0;latency=part.get('batch_login_latency',{})
            elif mode.startswith('offline_'):unit='storage_submissions/s';rate=part.get('storage_submitted_per_second',0);latency=part.get('ack_latency',{})
            else:unit='recipient_deliveries/s';rate=part.get('delivery_per_second',0);latency=part.get('delivery_latency',{})
            expected=part.get('expected',part.get('expected_deliveries',0))
            if mode.startswith('offline_') and fetch is None:expected=part.get('expected_stored',0)
            actual=part.get('stored_offline_submitted',0) if mode.startswith('offline_') and fetch is None else part.get('received_unique',0)
            client=usage.get('processes',{}).get('client',{})
            key=(case,cfg.get('pairs',cfg.get('recipients')),cfg.get('messages_per_pair',cfg.get('messages')),cfg.get('message_bytes'),tuple(sorted((route,routes.count(route)) for route in set(routes))),unit)
            sampling=job['sampling'];instrument_ok=not sampling or bool(usage) and not resources.get('errors')
            ok=bool(r.get('ok')) and instrument_ok and not job.get('result',{}).get('error')
            rows.append(dict(case=case,role=job['role'],job=job['name'],sampling=sampling,key=key,ok=ok,actual=actual,expected=expected,rate=rate,unit=unit,p95_ms=latency.get('p95_ms'),p99_ms=latency.get('p99_ms'),metric='batch_login_latency' if fetch is not None else 'ack_latency' if mode.startswith('offline_') else 'delivery_latency',phase_seconds=usage.get('seconds',part.get('seconds')),short_window=usage.get('short_window') if sampling else None,periodic_samples=usage.get('periodic_samples'),client_avg_cpu=client.get('avg_cpu_percent'),backend_cpu_seconds=usage.get('backend_cpu_seconds'),resource_errors=resources.get('errors',[]),result_file=job.get('report_directory'),raw=r))
    return rows

def number(value,digits=2):return '—' if value is None else f'{value:.{digits}f}'
def save():
    rows=observations();payload=dict(environment=metadata,jobs=jobs,observations=rows,error=failure)
    (work/'results.json').write_text(json.dumps(payload,indent=2))
    lines=['# 15.4 resource-aware baseline','', 'Units and paths differ by case. CPU 100% means one logical CPU; short windows are not capacity evidence.','', '| Role / job | Case | ok | Actual/expected | Unit | Rate | P95 ms | Backend CPU s | Client CPU % | Short window |','| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for r in rows:lines.append(f"| {r['role']} / {r['job']} | {r['case']} | {r['ok']} | {r['actual']}/{r['expected']} | {r['unit']} | {r['rate']:.2f} | {number(r['p95_ms'],3)} | {number(r['backend_cpu_seconds'],3)} | {number(r['client_avg_cpu'])} | {r['short_window']} |")
    lines+=['','## Formal repeated configurations','', '| Case / concurrency / messages / bytes / routes | Successful/all | Median rate | Min/max | Median of run P95 (ms) |','| --- | --- | --- | --- | --- |']
    groups={}
    for row in rows:
        if row['role']=='formal':groups.setdefault(str(row['key']),[]).append(row)
    for key,group in groups.items():
        good=[r for r in group if r['ok']];rates=[r['rate'] for r in good];p95=[r['p95_ms'] for r in good if r['p95_ms'] is not None]
        lines.append(f"| {key} | {len(good)}/{len(group)} | {number(statistics.median(rates) if rates else None)} | {number(min(rates) if rates else None)}/{number(max(rates) if rates else None)} | {number(statistics.median(p95) if p95 else None,3)} |")
    if failure:lines+=['','Aborted: '+failure]
    lines+=['','P95 summaries are medians of individual run P95, not pooled message P95. Preparation, warmup, storage and retrieval are separate. Failed/short/unavailable results remain visible. Full resource samples and source environment are in each job report directory.']
    (work/'summary.md').write_text('\n'.join(lines)+'\n')
    formal=[r for r in rows if r['role']=='formal'];short=sum(r['short_window'] is True for r in formal);bad=sum(not r['ok'] for r in formal)
    analysis=['# 15.4 evidence and next experiments','',f'Formal observations: {len(formal)}; failed: {bad}; short-window observations: {short}.','', '## Observed evidence','']
    for key,group in groups.items():
        good=[r for r in group if r['ok']]
        if good:analysis.append(f"- {key}: {len(good)}/{len(group)} successful, median {statistics.median(r['rate'] for r in good):.2f} {good[0]['unit']}; windows {min(r['phase_seconds'] for r in good):.3f}..{max(r['phase_seconds'] for r in good):.3f}s.")
    analysis+=['','## Resource evidence by configuration','']
    for key,group in groups.items():
        good=[r for r in group if r['ok']];long=[r for r in good if r['short_window'] is False]
        if not long:
            analysis.append('- '+key+': all successful resource windows are short; no stable CPU interpretation.')
            continue
        clients=[r['client_avg_cpu'] for r in long if r['client_avg_cpu'] is not None]
        backend=[r['backend_cpu_seconds']/r['phase_seconds']*100 for r in long if r['backend_cpu_seconds'] is not None and r['phase_seconds']>0]
        analysis.append('- '+key+': '+str(len(long))+' sufficiently sampled windows; median load-tool CPU '+number(statistics.median(clients) if clients else None)+'%, combined backend CPU '+number(statistics.median(backend) if backend else None)+'% (100%=one CPU).')
        if clients and statistics.median(clients)>=80:
            analysis.append('  Observation: the single-process load tool consumes much of one CPU. Hypothesis: its JSON/event-loop work may constrain throughput. Next check: compare an equivalent workload with more load-generator capacity before claiming a server limit; backend work can also contribute.')
        # Separate component RSS/FD observations; unavailable fields remain explicit.
        labels=sorted(set().union(*(r['raw'].get('resources',{}).get('phases',{}).get('retrieval' if 'retrieval' in r['raw'] else 'store' if r['raw'].get('mode','').startswith('offline_') else 'measure',{}).get('processes',{}) for r in long)))
        details=[]
        for label in labels:
            values=[]
            for r in long:
                phase='retrieval' if 'retrieval' in r['raw'] else 'store' if r['raw'].get('mode','').startswith('offline_') else 'measure'
                v=r['raw'].get('resources',{}).get('phases',{}).get(phase,{}).get('processes',{}).get(label,{})
                if v.get('available'):values.append(v)
            rss=[v['rss_peak_mib'] for v in values if v.get('rss_peak_mib') is not None];fd=[v['fd_peak'] for v in values if v.get('fd_peak') is not None]
            details.append(label+': observed max RSS '+number(max(rss) if rss else None)+' MiB, FD '+(str(max(fd)) if fd else 'unavailable'))
        analysis.append('  '+ '; '.join(details))
    analysis+=['','## Limits and hypotheses','', 'Short windows and tick quantization limit CPU interpretation. Process RSS/FD values are sampled maxima. MySQL is shared; unavailable metrics are not zero. Observer CPU and phase handshake overhead are part of the experiment environment. The sampling on/off calibration, if selected, is a small comparison and does not establish a precise overhead percentage.', '', 'Nginx cases mix local and remote routes; their aggregate rate does not isolate proxy overhead. Two backend nodes provide more total IO threads than one. The single VM also hosts the load tool and database, and background services can interfere.', '', 'Code candidates: connection identity lookup scans a locked map; remote routing makes synchronous DB/Redis calls; group routing iterates recipients. These are code facts, not measured function-level bottlenecks. High CPU suggests a CPU-limited candidate; low CPU can still indicate blocking. No component is declared the unique bottleneck from this report.', '', '## Next experiment','', 'First select a successful sufficiently long baseline and check both load-tool and backend CPU. Then vary one factor (concurrency, message bytes or recipient count) while retaining build/log settings and correctness checks. For synchronous DB/Redis hypotheses, add narrowly scoped timing before changing architecture. Optimize one confirmed candidate in module16 and rerun identical configurations. If evidence is insufficient, keep the conclusion unresolved.']
    (work/'analysis.md').write_text('\n'.join(analysis)+'\n')

def invoke(name,script,options,role='formal',sampling=True):
    global active,active_record
    record=dict(name=name,role=role,sampling=sampling,command=[script]+options,log=str(work/(name+'.log')));jobs.append(record);active_record=record
    env=dict(os.environ,CHAT_BENCH_SAMPLE='1' if sampling else '0');env.pop('CHAT_BENCH_MONITOR',None)
    print('RUN',name,'sampling='+str(sampling),flush=True)
    try:
        with open(record['log'],'w') as log:
            active=subprocess.Popen(['python3',str(root/'tests'/script)]+options,env=env,stdout=log,stderr=log)
            record['pid']=active.pid
            while active.poll() is None:time.sleep(.1)
            record['exit_code']=active.returncode;active=None
        collect(record);save()
        if record['exit_code']!=0:raise RuntimeError(name+' failed; inspect '+record['log'])
        if not record.get('result') or record['result'].get('error'):raise RuntimeError(name+' missing or failed result')
        print('PASS',name,flush=True);return record['result']
    finally:
        if active and active.poll() is None:active.terminate();active.wait(timeout=30)
        active=None;collect(record);save();active_record=None

def terminate(signum,frame):raise KeyboardInterrupt()
signal.signal(signal.SIGTERM,terminate)
if args.report_only:
    save();print('Regenerated:',work);sys.exit(0)
try:
    if args.overhead_check:
        for sampling in (False,True):invoke('calibration-'+str(sampling),'bench_baseline_test.py',['--pairs','3','--messages','20','--repeats','1','--topologies','single_direct'],role='calibration',sampling=sampling)
    if 'single' in args.scenarios:
        if args.profile=='quick':invoke('single-quick','bench_baseline_test.py',['--pairs','3','--messages','20','--bytes',str(args.bytes),'--repeats',str(args.repeats)])
        else:
            for topology in ('single_direct','redis_direct','nginx_entry'):
                pilot=invoke('pilot-'+topology,'bench_baseline_test.py',['--pairs',str(args.pairs),'--messages','50','--bytes',str(args.bytes),'--repeats','1','--topologies',topology],role='pilot')
                seconds=pilot['runs'][0]['measurement']['seconds'];count=max(50,min(10000,200000//args.pairs,math.ceil(50*5/max(seconds,.001))))
                invoke('single-'+topology,'bench_baseline_test.py',['--pairs',str(args.pairs),'--messages',str(count),'--bytes',str(args.bytes),'--repeats',str(args.repeats),'--topologies',topology])
    if 'group' in args.scenarios:
        recipients=['2'] if args.profile=='quick' else list(map(str,args.recipients));counts_file=None
        if args.profile=='full':
            pilot=invoke('pilot-group','bench_group_test.py',['--recipients']+recipients+['--messages','20','--bytes',str(args.bytes)],role='pilot')
            counts={r['case']:max(20,min(1000,math.ceil(20*5/max(r['measurement']['seconds'],.001)))) for r in pilot['runs'] if r.get('mode')=='group_online'}
            counts_file=work/'group-counts.json';counts_file.write_text(json.dumps(counts,indent=2))
        for repeat in range(1,args.repeats+1):
            options=['--recipients']+recipients+['--messages','5' if args.profile=='quick' else '50','--bytes',str(args.bytes)]
            if counts_file:options+=['--counts-file',str(counts_file)]
            invoke('group-'+str(repeat),'bench_group_test.py',options)
    formal=observations();groups={}
    for row in formal:
        if row['role']=='formal':groups.setdefault(str(row['key']),[]).append(row)
    if not groups or any(len(group)!=args.repeats or not all(row['ok'] for row in group) for group in groups.values()):
        raise RuntimeError('formal repeated configurations incomplete or failed')
except BaseException as e:
    failure=type(e).__name__+': '+str(e);raise
finally:
    if active and active.poll() is None:active.terminate();active.wait(timeout=30)
    if active_record:collect(active_record)
    save();print('Reports:',work,flush=True)
