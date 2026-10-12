"""15.3 interruption preserves failure reports and removes only owned group/accounts."""
import json,subprocess,time
from pathlib import Path
root=Path(__file__).resolve().parents[1]
p=subprocess.Popen(['python3',str(root/'tests/bench_group_test.py'),'--recipients','2','--messages','1000'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
try:
    first=p.stdout.readline();assert first.startswith('Run directory:'),first;work=Path(first.split(':',1)[1].strip());end=time.monotonic()+15
    while not list(work.glob('*.ready')):
        assert p.poll() is None;assert time.monotonic()<end;time.sleep(.01)
    p.terminate();out,err=p.communicate(timeout=20);assert p.returncode!=0,(out,err)
    report=json.loads((work/'results.json').read_text());assert 'KeyboardInterrupt' in report['error'],report
    assert report['environment']['cleanup_errors']==[] and report['runs'] and not report['runs'][0]['ok'],report
    assert not list(work.glob('*-config.json'))
    token=report['environment']['run_id'];names=["'b153_"+token+'_2_'+str(i)+"'" for i in range(3)]
    config='[client]\nuser=chat_module_test\npassword=Module02_test_only!\nhost=127.0.0.1\nprotocol=tcp\n'
    for q in ['SELECT COUNT(*) FROM User WHERE name IN ('+','.join(names)+')',"SELECT COUNT(*) FROM AllGroup WHERE groupname='b153_"+token+"_2'"]:
        r=subprocess.run(['mysql','--defaults-file=/dev/stdin','-N','chat_module_test','-e',q],input=config,text=True,capture_output=True,check=True);assert r.stdout.strip()=='0',r.stdout
    print('PASS interrupted group runner preserved partial report and cleaned owned groups/accounts/children')
    print('Reports:',work)
finally:
    if p.poll() is None:p.terminate();p.communicate(timeout=20)
