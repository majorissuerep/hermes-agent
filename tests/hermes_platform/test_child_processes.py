"""A local helper is reaped only with proven dead parent and matching child identity."""
import json
import os
import subprocess
import sys


def test_real_local_child_registration_and_orphan_reaping(tmp_path, monkeypatch):
    from hermes_platform import child_processes as children
    ledger = tmp_path / 'children.json'
    monkeypatch.setattr(children, '_ledger_path', lambda: ledger)
    helper = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    expired_parent = subprocess.Popen([sys.executable, '-c', 'pass'])
    try:
        parent_start = children.process_create_time(expired_parent.pid)
        expired_parent.wait(timeout=10)
        assert children.register_child(helper.pid, 'mcp-helper', project_root=tmp_path)
        assert children.reap_orphaned_mcp_helpers(project_root=tmp_path) == []
        row = json.loads(ledger.read_text())[0]
        assert row['spawner_pid'] == os.getpid()
        row['spawner_pid'] = expired_parent.pid
        row['spawner_create'] = parent_start
        original_start = row['create_time']
        row['create_time'] = original_start + 1000
        ledger.write_text(json.dumps([row]))
        assert children.reap_orphaned_mcp_helpers(project_root=tmp_path) == []
        assert helper.poll() is None
        row['create_time'] = original_start
        ledger.write_text(json.dumps([row]))
        assert children.reap_orphaned_mcp_helpers(project_root=tmp_path) == [helper.pid]
        helper.wait(timeout=10)
        assert children.ledger_entries(project_root=tmp_path) == []
    finally:
        for process in (helper, expired_parent):
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
