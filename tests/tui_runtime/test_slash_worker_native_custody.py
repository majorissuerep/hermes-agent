"""A trusted slash worker receives one key FD without revoking its owning session."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.platforms("linux", "macos")


@pytest.mark.parametrize("fail_spawn", [False, True], ids=["encrypted-read", "spawn-failure"])
def test_slash_worker_scopes_and_closes_native_key_fd(tmp_path, monkeypatch, fail_spawn):
    from hermes_cli.config import save_config
    from hermes_security import io, vault
    from hermes_security.session_authority import start_session_authority
    from tests.e2e.vault_home import seal_fixture_home

    home = tmp_path / "owner"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(home))
    seal_fixture_home(home)
    save_config({"agent": {"max_turns": 37}}, strip_defaults=False)
    io.write_text(home / ".env", "", purpose="env")
    original = (home / "config.yaml").read_bytes()
    authority = start_session_authority(home, "slash-custody-fixture")
    from tui_runtime import server

    inherited = []
    real_spawn = subprocess.Popen
    code = (
        f"import sys; sys.path.insert(0, {str(ROOT)!r}); "
        "from hermes_security.handoff import adopt_inherited_key; "
        "assert adopt_inherited_key(); import json; "
        "from hermes_security.io import read_text; "
        f"assert 'max_turns: 37' in read_text({str(home / 'config.yaml')!r}, purpose='config'); "
        "request=json.loads(sys.stdin.readline()); "
        "print(json.dumps({'id':request['id'],'ok':True,'output':'owned-encrypted-read'}),flush=True)"
    )

    def launch(_argv, **options):
        inherited.extend(options.get("pass_fds", ()))
        if fail_spawn:
            raise OSError("synthetic slash worker spawn refusal")
        return real_spawn([sys.executable, "-I", "-c", code], **options)

    monkeypatch.setattr(server, "subprocess", SimpleNamespace(**{**vars(subprocess), "Popen": launch}))
    worker = None
    try:
        if fail_spawn:
            with pytest.raises(OSError, match="synthetic slash worker"):
                server._SlashWorker(authority.session_id, "", profile_home=str(home))
            assert inherited, "a trusted worker spawn must receive its own descriptor"
            for fd in inherited:
                with pytest.raises(OSError):
                    os.fstat(fd)
        else:
            worker = server._SlashWorker(authority.session_id, "", profile_home=str(home))
            assert worker.run("/status") == "owned-encrypted-read"
        assert (home / "config.yaml").read_bytes() == original
        assert vault.is_unlocked(home) and authority.is_live()
    finally:
        if worker is not None:
            worker.close()
        authority.close()
        vault.clear_vault_cache()


def test_closed_slash_session_cannot_borrow_a_remaining_sessions_key_fd(tmp_path, monkeypatch):
    from hermes_cli.config import save_config
    from hermes_security import io, vault
    from hermes_security.errors import VaultLockedError
    from hermes_security.session_authority import SessionAuthorityError, start_session_authority
    from tests.e2e.vault_home import seal_fixture_home

    home = tmp_path / "owner"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_RUNTIME_DIR", str(tmp_path / "runtime"))
    seal_fixture_home(home)
    save_config({"agent": {"max_turns": 37}}, strip_defaults=False)
    io.write_text(home / ".env", "", purpose="env")
    closed = start_session_authority(home, "closed-slash-session")
    remaining = start_session_authority(home, "remaining-slash-session")
    closed.close()
    from tui_runtime import server

    inherited = []
    real_spawn = subprocess.Popen
    code = (
        f"import sys; sys.path.insert(0, {str(ROOT)!r}); import json; from pathlib import Path; "
        "from hermes_security.handoff import adopt_inherited_key; assert adopt_inherited_key(); "
        "from hermes_security.io import read_text; from hermes_security.session_authority import inherited_grant; "
        f"raw=inherited_grant(Path({str(home)!r})); "
        f"seen={{'readable': 'max_turns: 37' in read_text({str(home / 'config.yaml')!r}, purpose='config'), "
        "'borrowed_remaining_session': bool(raw and json.loads(raw)['session_id']=='remaining-slash-session')}; "
        "request=json.loads(sys.stdin.readline()); "
        "print(json.dumps({'id':request['id'],'ok':True,'output':json.dumps(seen)}),flush=True)"
    )

    def launch(_argv, **options):
        inherited.extend(options.get("pass_fds", ()))
        return real_spawn([sys.executable, "-I", "-c", code], **options)

    monkeypatch.setattr(server, "subprocess", SimpleNamespace(**{**vars(subprocess), "Popen": launch}))
    worker, refused, observed = None, False, {}
    try:
        try:
            worker = server._SlashWorker(closed.session_id, "", profile_home=str(home))
            observed = json.loads(worker.run("/status"))
        except (VaultLockedError, SessionAuthorityError):
            refused = True
        assert refused and not inherited, {"refused": refused, "spawned": worker is not None, **observed}
        assert remaining.is_live() and vault.is_unlocked(home)
    finally:
        if worker is not None:
            worker.close()
        closed.close()
        remaining.close()
        vault.clear_vault_cache()
