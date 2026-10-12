"""15.4 suite interruption cascades cleanup and preserves phase/resource reports."""
import json,subprocess,time
from pathlib import Path
root=Path(__file__).resolve().parents[1]
p=subprocess.Popen(['python3',str(root/'tests/bench_suite.py'),'--profile','quick','--scenarios','single'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
try:
    first=p.stdout.readline();assert first.startswith('Suite directory:'),first;work=Path(first.split(':',1)[1].strip());end=time.monotonic()+20;child=None
    while True:
        assert time.monotonic()<end and p.poll() is None
        log=work/'single-quick.log'
        if log.exists():
            lines=log.read_text().splitlines()
            if lines and lines[0].startswith('Run directory:'):
                child=Path(lines[0].split(':',1)[1].strip())
                if list(child.glob('*.ready')):break
        time.sleep(.01)
    p.terminate();out,err=p.communicate(timeout=40);assert p.returncode!=0,(out,err)
    suite=json.loads((work/'results.json').read_text());assert 'KeyboardInterrupt' in suite['error'],suite
    original=json.loads((child/'results.json').read_text());assert original['environment']['cleanup_errors']==[] and original['runs'],original
    assert not original['runs'][0]['ok'] and 'resources' in original['runs'][0]
    assert list(child.glob('*-resources.json')) and not list(child.glob('*-config.json'))
    token=original['environment']['run_id'];names=["'b152_"+token+'_'+str(i)+"'" for i in range(6)]
    config='[client]\nuser=chat_module_test\npassword=Module02_test_only!\nhost=127.0.0.1\nprotocol=tcp\n'
    q='SELECT COUNT(*) FROM User WHERE name IN ('+','.join(names)+')'
    r=subprocess.run(['mysql','--defaults-file=/dev/stdin','-N','chat_module_test','-e',q],input=config,text=True,capture_output=True,check=True);assert r.stdout.strip()=='0'
    print('PASS suite interruption preserved failed result/resources and cascaded owned-service/account cleanup')
    print('Reports:',work)
finally:
    if p.poll() is None:p.terminate();p.communicate(timeout=40)
