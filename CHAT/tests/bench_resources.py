"""Linux /proc sampler with acknowledged C++ phase boundaries; no privileged tools."""
import json,os,select,socket,threading,time,uuid
from pathlib import Path
HZ=os.sysconf('SC_CLK_TCK');PAGE=os.sysconf('SC_PAGE_SIZE')
def process_sample(pid,identity=None):
    stamp=time.monotonic_ns();result=dict(pid=pid,monotonic_ns=stamp)
    try:
        raw=Path(f'/proc/{pid}/stat').read_text();fields=raw[raw.rfind(')')+2:].split()
        start=int(fields[19]);result['starttime']=start
        if identity is not None and start!=identity:return dict(result,available=False,reason='pid_reused')
        result.update(available=True,cpu_seconds=(int(fields[11])+int(fields[12]))/HZ,rss_mib=int(fields[21])*PAGE/1048576)
    except (OSError,ValueError,IndexError) as e:return dict(result,available=False,reason=type(e).__name__)
    try:result['fd_count']=len(list(Path(f'/proc/{pid}/fd').iterdir()))
    except OSError as e:result['fd_count']=None;result['fd_reason']=type(e).__name__
    return result

def system_sample():
    result=dict(monotonic_ns=time.monotonic_ns())
    for name,read in [('cpu_ticks',lambda:list(map(int,Path('/proc/stat').read_text().splitlines()[0].split()[1:]))),('load',lambda:os.getloadavg()),('mem_available_mib',lambda:next(int(x.split()[1])/1024 for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))]:
        try:result[name]=read()
        except (OSError,ValueError,StopIteration) as e:result[name]=None;result[name+'_reason']=type(e).__name__
    return result

def mysql_pid():
    for path in Path('/proc').glob('[0-9]*/comm'):
        try:
            if path.read_text().strip() in ('mysqld','mariadbd'):return int(path.parent.name)
        except OSError:pass
    return None

def nginx_roster(pid_file):
    if not pid_file:return {}
    try:
        pid=int(Path(pid_file).read_text());children=Path(f'/proc/{pid}/task/{pid}/children').read_text().split()
        return dict(nginx_master=pid,**{'nginx_worker_'+str(i):int(p) for i,p in enumerate(children)})
    except (OSError,ValueError):return {}

def validate_events(events):
    active=None;last=-1;completed=set();errors=[]
    for event in events:
        name=event.get('phase');edge=event.get('edge');stamp=event.get('monotonic_ns',-1)
        if stamp<last:errors.append('non-monotonic event clock')
        last=stamp
        if name not in ('preparation','warmup','measure','store','retrieval'):errors.append('unknown phase')
        if edge=='begin':
            if active is not None or name in completed:errors.append('overlapping/duplicate phase')
            if name=='warmup' and ('preparation' not in completed or 'measure' in completed or 'store' in completed):errors.append('warmup before preparation')
            if name in ('measure','store') and 'preparation' not in completed:errors.append('measurement before preparation')
            if name=='preparation' and (completed or active is not None):errors.append('preparation out of order')
            if name=='retrieval' and completed:errors.append('retrieval out of order')
            active=name
        elif edge=='end':
            if active!=name:errors.append('unmatched phase end')
            completed.add(name);active=None
        else:errors.append('invalid edge')
    if active is not None:errors.append('incomplete phase')
    if not events:errors.append('missing phase events')
    return errors

def summarize(samples,events):
    phases={};errors=validate_events(events)
    for begin,end in zip(events[::2],events[1::2]):
        if begin.get('edge')!='begin' or end.get('edge')!='end' or begin.get('phase')!=end.get('phase'):continue
        name=begin['phase'];first=samples[begin['snapshot']];last=samples[end['snapshot']]
        window=[s for s in samples if first['monotonic_ns']<=s['monotonic_ns']<=last['monotonic_ns']]
        seconds=(last['monotonic_ns']-first['monotonic_ns'])/1e9
        periodics=sum(s['kind']=='periodic' for s in window)
        phase=dict(seconds=seconds,event_seconds=(end['monotonic_ns']-begin['monotonic_ns'])/1e9,periodic_samples=periodics,short_window=seconds<1 or periodics<3,processes={})
        labels=set().union(*(s['processes'] for s in window))
        for label in labels:
            a=first['processes'].get(label,{});b=last['processes'].get(label,{})
            values=[s['processes'][label] for s in window if label in s['processes'] and s['processes'][label].get('available')]
            if not(a.get('available') and b.get('available') and a.get('pid')==b.get('pid') and a.get('starttime')==b.get('starttime')):
                phase['processes'][label]=dict(available=False,reason=b.get('reason',a.get('reason','missing boundary snapshot')));continue
            dt=(b['monotonic_ns']-a['monotonic_ns'])/1e9;cpu=max(0,b['cpu_seconds']-a['cpu_seconds'])
            fd=[v['fd_count'] for v in values if v.get('fd_count') is not None];rates=[]
            for x,y in zip(values,values[1:]):
                if x['pid']==y['pid'] and x['starttime']==y['starttime']:
                    d=(y['monotonic_ns']-x['monotonic_ns'])/1e9
                    if d>0:rates.append(max(0,y['cpu_seconds']-x['cpu_seconds'])/d*100)
            phase['processes'][label]=dict(available=True,pid=a['pid'],cpu_seconds=cpu,avg_cpu_percent=cpu/dt*100 if dt>0 else None,sampled_cpu_peak_percent=max(rates) if rates else None,rss_peak_mib=max(v['rss_mib'] for v in values),fd_peak=max(fd) if fd else None,fd_reason=None if fd else 'unavailable')
        phase['backend_cpu_seconds']=sum(v['cpu_seconds'] for k,v in phase['processes'].items() if k.startswith('server_') and v.get('available')) if any(k.startswith('server_') and v.get('available') for k,v in phase['processes'].items()) else None
        phase['nginx_cpu_seconds']=sum(v['cpu_seconds'] for k,v in phase['processes'].items() if k.startswith('nginx_') and v.get('available')) if any(k.startswith('nginx_') and v.get('available') for k,v in phase['processes'].items()) else None
        sys_a=first['system'];sys_b=last['system'];phase['system']={}
        if sys_a.get('cpu_ticks') and sys_b.get('cpu_ticks'):
            delta=[y-x for x,y in zip(sys_a['cpu_ticks'][:8],sys_b['cpu_ticks'][:8])];total=sum(delta)
            phase['system']['cpu_busy_percent']=100*(total-delta[3]-delta[4]-delta[7])/total if total>0 else None
            phase['system']['steal_percent']=100*delta[7]/total if total>0 else None
        memory=[s['system']['mem_available_mib'] for s in window if s['system'].get('mem_available_mib') is not None]
        phase['system']['min_mem_available_mib']=min(memory) if memory else None
        phases[name]=phase
    return dict(phases=phases,errors=errors,clock='CLOCK_MONOTONIC',cpu_unit='100% = one logical CPU; may exceed 100%',boundary_note='snapshots are sequential; event handshake adds overhead; process-specific timestamps used for CPU averages',mysql_scope='shared process, not exclusive test cost')

class ResourceMonitor:
    def __init__(self,output,roster,nginx_pid_file=None,interval=.2):
        self.output=Path(output);self.roster=dict(roster);self.nginx_pid_file=nginx_pid_file;self.interval=interval
        self.socket_path='/tmp/chat-bench-'+uuid.uuid4().hex[:16]+'.sock';self.listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.listener.bind(self.socket_path);os.chmod(self.socket_path,0o600);self.listener.listen(4);self.listener.settimeout(.1)
        self.samples=[];self.events=[];self.identities={};self.stopped=set();self.errors=[];self.stop=threading.Event();self.client=None
        self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start()
    def snapshot(self,kind):
        roster=dict(self.roster,**nginx_roster(self.nginx_pid_file));roster['client']=self.client
        data=dict(monotonic_ns=time.monotonic_ns(),kind=kind,processes={},system=system_sample())
        for label,pid in roster.items():
            if pid is None:data['processes'][label]=dict(available=False,reason='not_discovered');continue
            key=(label,pid)
            if key in self.stopped:item=dict(pid=pid,available=False,reason='identity_ended')
            else:
                item=process_sample(pid,self.identities.get(key))
                if item.get('available'):self.identities[key]=item['starttime']
                elif key in self.identities:self.stopped.add(key)
            data['processes'][label]=item
        self.samples.append(data);return len(self.samples)-1
    def loop(self):
        next_sample=time.monotonic()
        try:
            while not self.stop.is_set():
                if time.monotonic()>=next_sample:self.snapshot('periodic');next_sample=time.monotonic()+self.interval
                if not select.select([self.listener],[],[],min(.05,max(0,next_sample-time.monotonic())))[0]:continue
                conn,_=self.listener.accept()
                with conn:
                    conn.settimeout(1);raw=b''
                    while not raw.endswith(b'\n'):
                        part=conn.recv(4096)
                        if not part:raise RuntimeError('truncated phase event')
                        raw+=part
                        if len(raw)>4096:raise RuntimeError('phase event too large')
                    event=json.loads(raw);pid=int(event['pid'])
                    if self.client is not None and self.client!=pid:raise RuntimeError('monitor client PID changed')
                    self.client=pid;event['snapshot']=self.snapshot('boundary');self.events.append(event);conn.sendall(b'1')
        except Exception as e:self.errors.append(type(e).__name__+': '+str(e))
    def close(self):
        self.stop.set();self.thread.join(timeout=3);self.listener.close();Path(self.socket_path).unlink(missing_ok=True)
        summary=summarize(self.samples,self.events);summary['errors']+=self.errors
        if self.thread.is_alive():summary['errors'].append('monitor failed to stop')
        summary['sample_interval_seconds']=self.interval
        self.output.write_text(json.dumps(dict(summary=summary,events=self.events,samples=self.samples),indent=2))
        return summary
