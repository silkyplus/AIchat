"""SIGTERM comparison runner: child wrapper must finish owned-service/account cleanup."""
import json,os,signal,subprocess,sys,time
from pathlib import Path
root=Path(__file__).resolve().parents[1]
parent=root/'.runtime/bench';before=set(parent.glob('baseline-*'))
proc=subprocess.Popen([sys.executable,str(root/'tests/db_pool_comparison.py')],cwd=root,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
try:
    deadline=time.monotonic()+60;run=None
    while time.monotonic()<deadline:
        if proc.poll() is not None:raise AssertionError(proc.stdout.read())
        candidates=[p for p in set(parent.glob('baseline-*'))-before if (p/'redis_direct-1.ready').exists()]
        if candidates:run=candidates[0];break
        time.sleep(.05)
    assert run,'measurement stage not reached'
    proc.send_signal(signal.SIGTERM);output=proc.communicate(timeout=35)[0]
    assert proc.returncode!=0
    result=json.loads((run/'results.json').read_text())
    assert result['error'] and not result['environment']['cleanup_errors']
    for node in result['environment']['node_ids']:
        # No surviving child server with this unique node identity.
        for p in Path('/proc').glob('[0-9]*'):
            try:assert ('CHAT_SERVER_ID='+node).encode()+b'\0' not in (p/'environ').read_bytes()
            except (OSError,PermissionError):pass
    cfg='[client]\nuser=chat_module_test\npassword=Module02_test_only!\nhost=127.0.0.1\nprotocol=tcp\n'
    token=result['environment']['run_id']
    check=subprocess.run(['mysql','--defaults-file=/dev/stdin','-N','chat_module_test','-e',"SELECT COUNT(*) FROM User WHERE name LIKE 'b152_"+token+"_%'"],input=cfg,text=True,capture_output=True,check=True)
    assert check.stdout.strip()=='0'
    print('PASS comparison SIGTERM forwards to child, preserves partial report, removes owned services/accounts')
finally:
    if proc.poll() is None:proc.terminate();proc.communicate(timeout=35)
