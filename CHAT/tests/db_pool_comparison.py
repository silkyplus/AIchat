"""Module16: identical finite workloads, pool off/on, three repetitions."""
import json,os,subprocess,sys,uuid,statistics,signal,argparse
from pathlib import Path
root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
options=parser.add_mutually_exclusive_group()
options.add_argument('--long',action='store_true',help='20,000 cross-node messages per run, with resource sampling')
options.add_argument('--report-only',type=Path,help='rebuild summary without starting services')
args=parser.parse_args()
long_run=args.long
report_only=args.report_only is not None
def interrupted(signum,frame):raise KeyboardInterrupt()
signal.signal(signal.SIGTERM,interrupted)
work=args.report_only.resolve() if report_only else root/'.runtime/module16'/('comparison-'+uuid.uuid4().hex[:12])
work.mkdir(parents=True,exist_ok=report_only)
print('Reports:',work,flush=True)
jobs=json.loads((work/'jobs.json').read_text()) if report_only else []
for repeat in ([] if report_only else range(1,4)):
    # Alternate order to reduce fixed ordering bias.
    for mode in (['off','on'] if repeat%2 else ['on','off']):
        specifications=[('single',['tests/bench_baseline_test.py','--pairs','10','--messages','500','--repeats','1','--topologies','single_direct','redis_direct']),('group',['tests/bench_group_test.py','--recipients','2','5','--messages','100','--offline-messages','101'])]
        if long_run:specifications=[('single',['tests/bench_baseline_test.py','--pairs','10','--messages','2000','--repeats','1','--topologies','redis_direct'])]
        for kind,command in specifications:
            name=f'{repeat}-{mode}-{kind}'
            env=dict(os.environ,CHAT_DB_POOL='0' if mode=='off' else '1',CHAT_DB_POOL_SIZE='8',CHAT_DB_PROFILE=str(work/(name+'-%p-db.json')))
            if long_run:
                env.pop('CHAT_DB_PROFILE',None);env['CHAT_BENCH_SAMPLE']='1'
            print('RUN',name,flush=True)
            child=subprocess.Popen([sys.executable,*command],cwd=root,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                stdout,stderr=child.communicate()
            except BaseException:
                child.terminate();stdout,stderr=child.communicate(timeout=30)
                (work/(name+'.log')).write_text(stdout+stderr)
                raise
            (work/(name+'.log')).write_text(stdout+stderr)
            path=next((line.split(': ',1)[1] for line in stdout.splitlines() if line.startswith('Run directory: ')),None)
            jobs.append(dict(name=name,mode=mode,kind=kind,repeat=repeat,code=child.returncode,path=path))
            (work/'jobs.json').write_text(json.dumps(jobs,indent=2))
            if child.returncode:raise RuntimeError(name+' failed; inspect log')
            print('PASS',name,flush=True)
rows={}
for job in jobs:
    data=json.loads((Path(job['path'])/'results.json').read_text())
    assert not data.get('error') and not data['environment'].get('cleanup_errors')
    for r in data['runs']:
        assert r['ok']
        if 'measurement' not in r:continue
        m=r['measurement'];case=r.get('case',r.get('topology'))
        rate=m.get('delivery_per_second',0) or m.get('storage_submitted_per_second',0) or m.get('request_per_second',0)
        latency=(m.get('delivery_latency',{}) if m.get('received_unique',0) else m.get('ack_latency',{})).get('p95_ms')
        rows.setdefault(case,{}).setdefault(job['mode'],[]).append(dict(rate=rate,p95_ms=latency,seconds=m['seconds']))
summary={}
for case,modes in rows.items():
    summary[case]={mode:dict(repeats=len(v),median_rate=statistics.median(r['rate'] for r in v),median_p95_ms=statistics.median(r['p95_ms'] for r in v if r['p95_ms'] is not None),min_seconds=min(r['seconds'] for r in v)) for mode,v in modes.items()}
    assert all(summary[case][mode]['repeats']==3 for mode in ('off','on'))
    summary[case]['rate_ratio']=summary[case]['on']['median_rate']/summary[case]['off']['median_rate']
(work/'comparison.json').write_text(json.dumps(summary,indent=2))
lines=['# 模块16连接池对比','', '各配置关闭/开启各3轮，取中位数。投递和存储按收件人计数。短窗口不表示最大容量。','', '| 场景 | 关闭 次/秒 | 开启 次/秒 | 吞吐比 | 关闭 P95 ms | 开启 P95 ms |','| --- | --- | --- | --- | --- | --- |']
for case,r in summary.items():
    a,b=r['off'],r['on']
    lines.append(f"| {case} | {a['median_rate']:.2f} | {b['median_rate']:.2f} | {r['rate_ratio']:.2f} | {a['median_p95_ms']:.3f} | {b['median_p95_ms']:.3f} |")
(work/'summary.md').write_text('\n'.join(lines)+'\n')
print(json.dumps(summary,indent=2),flush=True)
