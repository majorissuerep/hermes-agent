"""Tests for tools/browser_lightpanda.py — the ``lightpanda serve`` launcher
Browser Use mode uses when ``browser.engine`` is ``lightpanda``."""

import json
import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

import tools.browser_lightpanda as lp

# The autouse _isolate fixture swaps _binary_supports_http_cache for a lambda;
# the probe test needs the real scoped capability function back.
_real_probe = lp._binary_supports_http_cache
pytestmark = pytest.mark.platforms('linux', 'macos')


class FakeProc:
    instances = {}

    def __init__(self, pid=4242, exit_code=None):
        self.pid = pid
        self.instances[pid] = self
        self._rc = exit_code
        self.terminated = False
        self.killed = False

    def poll(self):
        return self._rc

    def terminate(self):
        self.terminated = True
        self._rc = -15

    def kill(self):
        self.killed = True
        self._rc = -9

    def wait(self, timeout=None):
        return self._rc

    def cancel(self, timeout=None):
        self.terminate()
        return 130


pytest_plugins = ['tests.tools.test_browser_native_private_state']


@pytest.fixture(autouse=True)
def _isolate(native_state, monkeypatch):
    """Declared unit RAM/vendor seams; this suite never executes a Lightpanda binary."""
    from hermes_cli import browser_worker_store as store
    from hermes_platform import processes

    home, _user, _ram = native_state
    resource = store.resource(home, kind='lightpanda-http-cache')
    with resource.access():
        pass
    monkeypatch.setattr(lp, '_home_candidates', list)
    lp._CAPABILITIES.clear()
    monkeypatch.setattr(lp, '_binary_supports_http_cache', lambda binary, env: True)
    native_start, native_dead = processes.get_process_start_time, processes.process_record_is_dead
    monkeypatch.setattr(processes, 'get_process_start_time',
                        lambda pid: 111 if pid in FakeProc.instances else native_start(pid))
    monkeypatch.setattr(processes, 'process_record_is_dead',
                        lambda record: FakeProc.instances[record['pid']].poll() is not None
                        if record.get('pid') in FakeProc.instances else native_dead(record))
    monkeypatch.setattr(lp, '_process_record', lambda proc,binary,argv,env:
                        {'pid':proc.pid,'start_time':111,'executable':binary,'argv':list(argv),
                         'home':env['HOME'],'port':int(argv[argv.index('--port')+1])})
    monkeypatch.setattr(lp, '_record_matches', lambda record: record['pid'] in FakeProc.instances)
    FakeProc.instances.clear()
    lp._servers.clear()
    yield resource.tree
    lp._servers.clear()
    FakeProc.instances.clear()


def _exe(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class TestFindBinary:
    def test_prefers_path(self, tmp_path, monkeypatch):
        exe = _exe(tmp_path / "bin" / "lightpanda")
        monkeypatch.setenv("PATH", str(tmp_path / "bin"))
        monkeypatch.setattr("tools.browser_tool_install._merge_browser_path", lambda p: p)
        assert lp.find_lightpanda_binary() == str(exe)

    def test_falls_back_to_home_candidates(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))
        monkeypatch.setattr("tools.browser_tool_install._merge_browser_path", lambda p: p)
        exe = _exe(tmp_path / ".lightpanda" / "lightpanda")
        monkeypatch.setattr(lp, "_home_candidates", lambda: [exe])
        assert lp.find_lightpanda_binary() == str(exe)

    def test_none_when_absent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))
        monkeypatch.setattr("tools.browser_tool_install._merge_browser_path", lambda p: p)
        assert lp.find_lightpanda_binary() is None




class TestLaunch:
    def _launch(self, monkeypatch, *, proc=None, ready=True, stderr=b"", name='lp_test', **kw):
        calls = {}

        def fake_popen(argv, **kwargs):
            calls["argv"] = argv
            calls["kwargs"] = kwargs
            if stderr:
                kwargs["stderr"].write(stderr)
                kwargs["stderr"].flush()
            return proc or FakeProc(pid=4242+len(FakeProc.instances))

        monkeypatch.setattr(lp, "find_lightpanda_binary", lambda: "/opt/lightpanda")
        monkeypatch.setattr(lp, "_pick_free_loopback_port", lambda: 43111)
        monkeypatch.setattr(lp, "_cdp_ready", lambda url: ready)
        monkeypatch.setattr(lp, "_browser_env", lambda: {"PATH": "/usr/bin"})
        monkeypatch.setattr('hermes_platform.managed_supervision.SupervisedChild', fake_popen)
        server, err = lp.launch_lightpanda(name, **kw)
        return server, err, calls

    def test_missing_binary_returns_install_hint(self, monkeypatch):
        monkeypatch.setattr(lp, "find_lightpanda_binary", lambda: None)
        server, err = lp.launch_lightpanda("lp_test")
        assert server is None
        assert "browser.engine" in err
        assert lp.LIGHTPANDA_INSTALL_URL in err

    def test_spawns_serve_on_loopback_without_timeout_flag(self, monkeypatch, _isolate):
        server, err, calls = self._launch(monkeypatch)
        assert err is None
        assert calls["argv"] == [
            "/opt/lightpanda", "serve", "--host", "127.0.0.1", "--port", "43111",
            "--http-cache-dir", str(_isolate / "http-cache"),
        ]
        kw = calls["kwargs"]
        assert kw["stdin"] is subprocess.DEVNULL
        assert kw["stdout"] is subprocess.DEVNULL
        assert hasattr(kw["stderr"], "write")  # a log file, never a pipe
        assert kw['env']['PATH'] == '/usr/bin'
        assert Path(kw['env']['HOME']).is_relative_to(_isolate)
        assert Path(kw['env']['TMPDIR']).is_relative_to(_isolate.parent)
        if os.name != "nt":
            assert kw["start_new_session"] is True
        assert server.cdp_url == "http://127.0.0.1:43111"
        assert server.start_time == 111
        assert lp.get_server("lp_test") is server
        record = server.lease.record
        assert record['pid'] == server.proc.pid and record['port'] == 43111
        assert record['start_time'] == 111
        assert not (_isolate/'lp_test.json').exists()  # old control files are never active records

    def test_http_cache_dir_is_shared_across_sessions(self, monkeypatch, _isolate):
        _, _, first = self._launch(monkeypatch)
        with lp._servers_lock:
            lp._servers.clear()
        _, _, second = self._launch(monkeypatch)
        cache = str(_isolate / "http-cache")
        assert first["argv"][first["argv"].index("--http-cache-dir") + 1] == cache
        assert second["argv"][second["argv"].index("--http-cache-dir") + 1] == cache
        assert Path(cache).is_dir()
        assert not list(Path(cache).glob("*.json"))  # never confused with a session record

    def test_no_http_cache_flag_on_old_binary(self, monkeypatch, _isolate):
        monkeypatch.setattr(lp, "_binary_supports_http_cache", lambda binary, env: False)
        _, err, calls = self._launch(monkeypatch)
        assert err is None
        assert "--http-cache-dir" not in calls["argv"]
        assert calls["argv"][-1] == "43111"

    def test_http_cache_probe_caches_and_detects_flag(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lp, "_binary_supports_http_cache", _real_probe)
        lp._CAPABILITIES.clear()
        exe = _exe(tmp_path / "lightpanda")
        runs = []
        from tools.environments.local_env_policy import strip_local_vault_authority
        env = {'HOME':str(tmp_path), '__HERMES_INSTALL_OWNER_FD':'97'}

        def fake_run(argv, **kwargs):
            runs.append(argv)
            assert kwargs['env'] == {**strip_local_vault_authority(env), 'LIGHTPANDA_DISABLE_TELEMETRY':'true'}
            return subprocess.CompletedProcess(
                argv, returncode=0,
                stdout="--http-cache-dir <PATH>" if len(runs) == 1 else "",
            )

        monkeypatch.setattr(lp.subprocess, "run", fake_run)
        assert lp._binary_supports_http_cache(str(exe), env) is True
        assert lp._binary_supports_http_cache(str(exe), env) is True  # cached, single probe
        assert len(runs) == 1

        lp._CAPABILITIES.clear()

        def fake_run_old(argv, **kwargs):
            return subprocess.CompletedProcess(argv, returncode=0, stdout="no such flag")

        monkeypatch.setattr(lp.subprocess, "run", fake_run_old)
        assert lp._binary_supports_http_cache(str(exe), env) is False

        def fake_run_hangs(argv, **kwargs):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=3.0)

        lp._CAPABILITIES.clear()
        monkeypatch.setattr(lp.subprocess, "run", fake_run_hangs)
        assert lp._binary_supports_http_cache(str(exe), env) is False

    def test_block_private_networks_flag(self, monkeypatch):
        _, err, calls = self._launch(monkeypatch, block_private_networks=True)
        assert err is None
        assert calls["argv"][-1] == "--block-private-networks"

    def test_early_exit_reports_stderr_tail(self, monkeypatch):
        server, err, _ = self._launch(
            monkeypatch, proc=FakeProc(exit_code=1), ready=False,
            stderr=b"info: starting\nFATAL app : unknown argument --bogus\n",
        )
        assert server is None
        assert "exited with code 1" in err
        assert "unknown argument --bogus" in err
        assert lp.get_server('lp_test') is not None  # failed private work remains discoverable

    def test_ready_timeout_terminates_child(self, monkeypatch, _isolate):
        monkeypatch.setattr(lp, "_READY_TIMEOUT_S", 0.05)
        monkeypatch.setattr(lp, "_POLL_INTERVAL_S", 0.01)
        proc = FakeProc()
        server, err, _ = self._launch(monkeypatch, proc=proc, ready=False)
        assert server is None
        assert 'did not establish' in err
        assert proc.terminated is True
        assert not (_isolate / "lp_test.json").exists()
        assert lp.get_server('lp_test') is not None

    def test_spawn_failure_is_reported(self, monkeypatch):
        monkeypatch.setattr(lp, "find_lightpanda_binary", lambda: "/opt/lightpanda")
        monkeypatch.setattr(lp, "_pick_free_loopback_port", lambda: 1)
        monkeypatch.setattr(lp, "_browser_env", dict)

        def boom(*a, **k):
            raise OSError("exec format error")

        monkeypatch.setattr('hermes_platform.managed_supervision.SupervisedChild', boom)
        server, err = lp.launch_lightpanda("lp_test")
        assert server is None
        assert "exec format error" in err


class TestStop:
    def _seed(self, monkeypatch, name='lp_test', alive=True):
        proc = FakeProc(pid=777+len(lp._servers))
        server, error, _calls = TestLaunch()._launch(monkeypatch, proc=proc, name=name)
        assert error is None
        if not alive:
            proc._rc = 0
        return server

    def test_stop_uses_owned_supervisor_and_removes_its_exact_record(self, monkeypatch):
        server = self._seed(monkeypatch)
        lp.stop_lightpanda('lp_test')
        assert server.proc.terminated
        assert lp.get_server('lp_test') is None
        assert not (server.lease.resource.path/'processes'/str(server.proc.pid)).exists()

    def test_unconfirmed_owned_cleanup_retains_tracking_and_previous_archive(self, monkeypatch):
        server = self._seed(monkeypatch)
        prior = (server.lease.home/server.lease.resource.relative).read_bytes()
        monkeypatch.setattr(server.proc, 'cancel', lambda: None)
        with pytest.raises(RuntimeError, match='unconfirmed'):
            lp.stop_lightpanda('lp_test')
        assert lp.get_server('lp_test') is server
        assert (server.lease.home/server.lease.resource.relative).read_bytes() == prior

    def test_stop_dead_server_drops_only_verified_own_record(self, monkeypatch):
        server = self._seed(monkeypatch, alive=False)
        lp.stop_lightpanda('lp_test')
        assert not server.proc.terminated and lp.get_server('lp_test') is None

    def test_stop_unknown_session_is_noop(self):
        lp.stop_lightpanda('untracked')

    def test_stop_all(self, monkeypatch):
        a = self._seed(monkeypatch, name='one')
        b = self._seed(monkeypatch, name='two')
        lp.stop_all_lightpanda()
        assert a.proc.terminated and b.proc.terminated
        assert not lp._servers


class TestReapOrphans:
    def _record(self, _isolate, name, *, pid=999, owner_pid=1, port=43111, start_time=111):
        from tools.browser_lightpanda_state import legacy_path
        from hermes_constants import get_hermes_home
        source = legacy_path(get_hermes_home())
        source.mkdir(parents=True, exist_ok=True)
        record = source/(name+'.json')
        record.write_text(json.dumps({'pid':pid,'owner_pid':owner_pid,'port':port,'start_time':start_time}))
        return record

    def test_live_other_owner_is_skipped(self, _isolate):
        path = self._record(_isolate, 'live', owner_pid=os.getpid())
        original = path.read_bytes()
        assert lp.reap_orphaned_lightpanda() == 0 and path.read_bytes() == original

    def test_own_tracked_session_is_skipped(self, monkeypatch):
        server = TestStop()._seed(monkeypatch)
        assert lp.reap_orphaned_lightpanda() == 0
        assert not server.proc.terminated and lp.get_server('lp_test') is server

    def test_dead_owner_raw_record_never_authorizes_signals(self, _isolate):
        path = self._record(_isolate, 'orphan', owner_pid=2**30)
        original = path.read_bytes()
        assert lp.reap_orphaned_lightpanda() == 0 and path.read_bytes() == original

    def test_own_verified_dead_server_is_reconciled(self, monkeypatch):
        server = TestStop()._seed(monkeypatch, alive=False)
        assert lp.reap_orphaned_lightpanda() == 1
        assert not server.proc.terminated and lp.get_server('lp_test') is None

    def test_unverified_pid_is_never_signalled(self, _isolate):
        path = self._record(_isolate, 'unknown', start_time=None)
        assert lp.reap_orphaned_lightpanda() == 0 and path.exists()

    def test_corrupt_record_is_preserved_for_explicit_recovery(self, _isolate):
        path = self._record(_isolate, 'damaged')
        path.write_bytes(b'{not a record')
        assert lp.reap_orphaned_lightpanda() == 0 and path.read_bytes() == b'{not a record'
