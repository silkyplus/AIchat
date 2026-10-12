"""15.2 runner interruption must preserve partial reports and clean its own resources."""
import json,subprocess,time
from pathlib import Path
root=Path(__file__).resolve().parents[1]
p=subprocess.Popen(['python3',str(root/'tests/bench_baseline_test.py'),'--pairs','3','--messages','10000','--repeats','1'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
try:
    first=p.stdout.readline();assert first.startswith('Run directory:'),first
    work=Path(first.split(':',1)[1].strip());end=time.monotonic()+15
    while not list(work.glob('*.ready')):
        assert p.poll() is None,'runner stopped before barrier'
        assert time.monotonic()<end,'ready deadline';time.sleep(.01)
    p.terminate();out,err=p.communicate(timeout=20)
    assert p.returncode!=0,(out,err)
    report=json.loads((work/'results.json').read_text())
    assert 'KeyboardInterrupt' in report['error'],report
    assert report['environment']['cleanup_errors']==[],report
    assert report['runs'] and not report['runs'][0]['ok'],report
    assert not list(work.glob('*-config.json')),'account configuration left behind'
    # Known test names only; never mutate or signal unrelated services.
    token=report['environment']['run_id'];names=["'b152_"+token+'_'+str(i)+"'" for i in range(6)]
    config='[client]\nuser=chat_module_test\npassword=Module02_test_only!\nhost=127.0.0.1\nprotocol=tcp\n'
    query='SELECT COUNT(*) FROM User WHERE name IN ('+','.join(names)+')'
    r=subprocess.run(['mysql','--defaults-file=/dev/stdin','-N','chat_module_test','-e',query],input=config,text=True,capture_output=True,check=True)
    assert r.stdout.strip()=='0',r.stdout
    print('PASS interrupted runner retained failed C++ report, removed temporary credentials and test accounts, stopped owned children')
    print('Reports:',work)
finally:
    if p.poll() is None:p.terminate();p.communicate(timeout=20)
