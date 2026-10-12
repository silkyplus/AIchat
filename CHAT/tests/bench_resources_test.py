"""Sampler checks: units, identity, missing permissions, phase integrity, known CPU load."""
import os,subprocess,time
from unittest.mock import patch
from bench_resources import process_sample,summarize,validate_events
self_sample=process_sample(os.getpid());assert self_sample['available'] and self_sample['rss_mib']>0 and self_sample['fd_count']>=3
assert process_sample(os.getpid(),self_sample['starttime']+1)['reason']=='pid_reused'
with patch('bench_resources.Path.iterdir',side_effect=PermissionError('fixture')):
    denied=process_sample(os.getpid());assert denied['available'] and denied['fd_count'] is None and denied['fd_reason']=='PermissionError'
assert validate_events([])
assert validate_events([dict(phase='measure',edge='begin',monotonic_ns=1)])
assert validate_events([dict(phase='preparation',edge='begin',monotonic_ns=2),dict(phase='preparation',edge='end',monotonic_ns=1)])
events=[dict(phase='retrieval',edge='begin',monotonic_ns=0,snapshot=0),dict(phase='retrieval',edge='end',monotonic_ns=500000000,snapshot=1)]
samples=[]
for i in range(2):
    samples.append(dict(monotonic_ns=i*500000000,kind='boundary',system={'cpu_ticks':[0,0,0,0,0,0,0,0],'mem_available_mib':100},processes={'client':dict(available=True,pid=1,starttime=1,monotonic_ns=i*500000000,cpu_seconds=1+i,rss_mib=10+i,fd_count=5+i)}))
s=summarize(samples,events);assert not s['errors'];c=s['phases']['retrieval']['processes']['client'];assert c['avg_cpu_percent']==200 and c['rss_peak_mib']==11 and c['fd_peak']==6
busy=idle=None
try:
    busy=subprocess.Popen(['python3','-u','-c',"print('ready');exec('while True: pass')"],stdout=subprocess.PIPE,text=True);assert busy.stdout.readline().strip()=='ready'
    idle=subprocess.Popen(['python3','-u','-c',"import time;print('ready');time.sleep(60)"],stdout=subprocess.PIPE,text=True);assert idle.stdout.readline().strip()=='ready'
    a=process_sample(busy.pid);b=process_sample(idle.pid);time.sleep(.5);aa=process_sample(busy.pid);bb=process_sample(idle.pid)
    assert aa['cpu_seconds']-a['cpu_seconds']>.01 and aa['cpu_seconds']-a['cpu_seconds']>bb['cpu_seconds']-b['cpu_seconds']
finally:
    for p in (busy,idle):
        if p and p.poll() is None:p.terminate();p.wait(timeout=3)
assert not process_sample(busy.pid)['available']
print('PASS CPU units (including >100%), RSS/FD, PID identity/exit, unavailable FD, phase errors, busy versus idle')
