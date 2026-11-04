"""Operation IPC works through a real read-only mount without widening child permissions."""
import shutil
import subprocess
import sys

import pytest


@pytest.mark.platforms("linux")
def test_fifo_operations_need_no_writable_regular_file_in_readonly_namespace():
    if not shutil.which("unshare") or not shutil.which("mount"):
        pytest.skip("native user/mount namespaces need unshare and mount")
    code = """
import subprocess, sys, threading
from hermes_security.pipe_authority import PipeServer, redeem
if subprocess.run(['mount', '--make-rprivate', '/'], capture_output=True).returncode:
    raise SystemExit(77)
server = PipeServer(lambda request: {'ok': True, 'value': request['value']})
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
path = str(server.path)
bound = False
try:
    if subprocess.run(['mount', '--bind', path, path], capture_output=True).returncode:
        raise SystemExit(77)
    bound = True
    if subprocess.run(['mount', '-o', 'remount,bind,ro', path], capture_output=True).returncode:
        raise SystemExit(77)
    grant = {'pipe': path, 'home': '/synthetic-home', 'session_id': 'synthetic-session',
             'token': 'synthetic-operation-only-grant'}
    reply = redeem(grant, {'op': 'test', 'value': 'public-operation-result'})
    assert reply['ok'] is True and reply['value'] == 'public-operation-result'
    assert (server.path / 'lock').read_bytes() == b''
finally:
    if bound:
        subprocess.run(['umount', path], check=True)
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
"""
    result = subprocess.run(["unshare", "--user", "--map-root-user", "--mount", sys.executable, "-c", code],
                            capture_output=True, text=True, timeout=30, check=False)
    if result.returncode == 77 or ("unshare failed" in result.stderr and "Operation not permitted" in result.stderr):
        pytest.skip("native user/mount namespaces are unavailable on this host")
    assert result.returncode == 0, result.stderr
