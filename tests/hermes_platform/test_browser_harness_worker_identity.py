"""Read-side import admission recognizes the exact worker shape; native facts are unit seams."""
import os
from types import SimpleNamespace

import psutil
import pytest

from hermes_platform.browser_process import require_harness_source_idle

pytestmark = pytest.mark.platforms('linux', 'macos')


def _process(monkeypatch, command, environment, *, unreadable=False):
    def environ():
        if unreadable:
            raise psutil.AccessDenied()
        return environment
    process = SimpleNamespace(info={'uids':SimpleNamespace(effective=os.geteuid()), 'cmdline':command},
                              environ=environ)
    monkeypatch.setattr('hermes_platform.browser_process.psutil.process_iter', lambda _fields:[process])


@pytest.mark.parametrize('unreadable', [False, True])
def test_live_relocated_private_worker_prevents_source_import_without_effects(tmp_path, monkeypatch, unreadable):
    source = tmp_path/'legacy-workspace';source.mkdir()
    original = source/'sole-copy';original.write_bytes(b'original synthetic browser work')
    before = original.stat()
    command = ['/selected/PM-python', '/relocated-install/tools/browser_harness_worker.py']
    _process(monkeypatch, command, {'BH_AGENT_WORKSPACE':str(source)}, unreadable=unreadable)
    with pytest.raises(RuntimeError, match='cannot be inspected' if unreadable else 'process is live'):
        require_harness_source_idle(source, check_alive=lambda:None)
    assert original.read_bytes() == b'original synthetic browser work'
    assert original.stat().st_ino == before.st_ino


@pytest.mark.parametrize('command', [
    ['/selected/PM-python', '-c', '/relocated-install/tools/browser_harness_worker.py'],
    ['/selected/PM-python', '/relocated-install/not-tools/browser_harness_worker.py'],
    ['/selected/PM-python', '/relocated-install/tools/browser_harness_worker.py.extra'],
    ['/selected/PM-python', '-m', 'unrelated.browser_harness.run'],
])
def test_lookalike_arguments_are_not_harness_worker_owners(tmp_path, monkeypatch, command):
    source = tmp_path/'source';source.mkdir()
    _process(monkeypatch, command, {'BH_AGENT_WORKSPACE':str(source)})
    require_harness_source_idle(source, check_alive=lambda:None)


@pytest.mark.parametrize('module', ['browser_harness.run', 'browser_harness.daemon'])
def test_original_sdk_module_processes_remain_live_source_owners(tmp_path, monkeypatch, module):
    source = tmp_path/'source';source.mkdir()
    _process(monkeypatch, ['/selected/PM-python', '-m', module], {'BH_AGENT_WORKSPACE':str(source)})
    with pytest.raises(RuntimeError, match='process is live'):
        require_harness_source_idle(source, check_alive=lambda:None)


def test_matching_worker_for_a_different_source_does_not_claim_this_source(tmp_path, monkeypatch):
    source = tmp_path/'source';source.mkdir()
    other = tmp_path/'other';other.mkdir()
    _process(monkeypatch, ['/selected/PM-python', '/relocated-install/tools/browser_harness_worker.py'],
             {'HOME':str(other), 'BH_HOME':str(other), 'BH_CONFIG_DIR':str(other), 'BH_AGENT_WORKSPACE':str(other)})
    require_harness_source_idle(source, check_alive=lambda:None)
