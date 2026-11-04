"""Protected workspaces exist only on a proven RAM-only mount and fail closed otherwise."""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from hermes_platform.memory_protection import RamProof, ram_only_directory_proof
from hermes_security.errors import VaultError
from hermes_security.protected_workspace import (
    ProtectedWorkspaceUnavailable,
    protected_workspace,
    protected_workspace_base,
    protected_workspace_status,
)


@pytest.fixture
def base(tmp_path: Path) -> Path:
    """An empty caller-chosen base; ``tmp_path`` itself also holds the isolated Hermes home."""
    directory = tmp_path / "workspace-base"
    directory.mkdir()
    return directory


def _always_proven(_path: Path) -> RamProof:
    return RamProof(True, "ramfs", "synthetic proof for the success path")


def _never_proven(_path: Path) -> RamProof:
    return RamProof(False, "tmpfs", "synthetic: tmpfs pages can be written to active swap")


def _assert_owner_only_workspace(workspace: Path, base: Path, purpose: str) -> None:
    info = workspace.lstat()
    assert stat.S_ISDIR(info.st_mode)
    assert stat.S_IMODE(info.st_mode) == 0o700
    assert info.st_uid == os.geteuid()
    assert workspace.parent == Path(os.path.realpath(base))
    assert workspace.name.startswith(f"hermes-{purpose}-")


def test_unavailable_is_a_fatal_vault_error() -> None:
    assert issubclass(ProtectedWorkspaceUnavailable, VaultError)


def test_unproven_base_fails_closed_without_creating_anything(base: Path) -> None:
    with pytest.raises(ProtectedWorkspaceUnavailable, match="active swap"):
        with protected_workspace("vendor-cli", base=base, prove=_never_proven):
            pytest.fail("an unproven workspace must not be entered")
    assert list(base.iterdir()) == []


def test_proven_workspace_is_owner_only_and_removed(base: Path) -> None:
    with protected_workspace("vendor-cli", base=base, prove=_always_proven) as workspace:
        _assert_owner_only_workspace(workspace, base, "vendor-cli")
        (workspace / "nested").mkdir()
        (workspace / "nested" / "scratch.bin").write_bytes(b"synthetic")
    assert list(base.iterdir()) == []


@pytest.mark.platforms("posix")
def test_sgid_base_keeps_its_mode_and_creates_an_owner_only_workspace(base: Path) -> None:
    """Native mode inheritance is real here; the RAM proof remains a labelled unit seam."""
    os.chown(base, -1, os.getegid())
    base.chmod(0o2700)
    assert stat.S_IMODE(base.stat().st_mode) == 0o2700
    with protected_workspace("group-runtime", base=base, prove=_always_proven) as workspace:
        _assert_owner_only_workspace(workspace, base, "group-runtime")
        assert stat.S_IMODE(base.stat().st_mode) == 0o2700
        (workspace / "private.txt").write_text("synthetic protected content", encoding="utf-8")
    assert list(base.iterdir()) == []


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("mode", [0o750, 0o770, 0o2770])
def test_created_workspace_rejects_shared_permissions_without_changing_them(base: Path, mode: int) -> None:
    from hermes_security.protected_workspace import _verify_created

    workspace = base / "unsafe"
    workspace.mkdir()
    os.chown(workspace, -1, os.getegid())
    workspace.chmod(mode)
    assert stat.S_IMODE(workspace.stat().st_mode) == mode
    with pytest.raises(ProtectedWorkspaceUnavailable, match="changed"):
        _verify_created(workspace, _always_proven)
    assert stat.S_IMODE(workspace.stat().st_mode) == mode


def test_workspace_is_removed_when_the_body_raises(base: Path) -> None:
    with pytest.raises(LookupError):
        with protected_workspace("vendor-cli", base=base, prove=_always_proven) as workspace:
            (workspace / "scratch.bin").write_bytes(b"synthetic")
            raise LookupError("body failed")
    assert list(base.iterdir()) == []


def test_workspace_already_removed_by_the_body_is_not_an_error(base: Path) -> None:
    with protected_workspace("vendor-cli", base=base, prove=_always_proven) as workspace:
        workspace.rmdir()
    assert list(base.iterdir()) == []


@pytest.mark.platforms("posix")
@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_cleanup_failure_is_reported(base: Path) -> None:
    sealed = None
    try:
        with pytest.raises(PermissionError):
            with protected_workspace("vendor-cli", base=base, prove=_always_proven) as workspace:
                sealed = workspace / "sealed"
                sealed.mkdir()
                (sealed / "scratch.bin").write_bytes(b"synthetic")
                sealed.chmod(0o500)
    finally:
        if sealed is not None:
            sealed.chmod(0o700)


def test_created_directory_must_itself_be_proven(base: Path) -> None:
    proven_base = os.path.realpath(base)

    def base_only(path: Path) -> RamProof:
        if os.path.realpath(path) == proven_base:
            return _always_proven(path)
        return _never_proven(path)

    with pytest.raises(ProtectedWorkspaceUnavailable):
        with protected_workspace("vendor-cli", base=base, prove=base_only):
            pytest.fail("a workspace that left the proven mount must not be entered")
    assert list(base.iterdir()) == []


@pytest.mark.parametrize("purpose", ["", "../escape", "Vendor", "a b", "x" * 64, "-lead"])
def test_purpose_must_be_a_short_label(base: Path, purpose: str) -> None:
    with pytest.raises(ValueError):
        with protected_workspace(purpose, base=base, prove=_always_proven):
            pytest.fail("an invalid purpose must not create a workspace")
    assert list(base.iterdir()) == []


def test_status_reports_without_creating(base: Path) -> None:
    assert protected_workspace_status(base, prove=_never_proven) == _never_proven(base)
    assert list(base.iterdir()) == []


@pytest.mark.platforms("linux")
def test_native_base_matches_host_proof(base: Path) -> None:
    """Fail-closed on the real host: an unproven base refuses and stays empty."""
    proof = protected_workspace_status(base)
    assert proof == ram_only_directory_proof(base)
    if proof.proven:
        with protected_workspace("native-check", base=base) as workspace:
            _assert_owner_only_workspace(workspace, base, "native-check")
    else:
        with pytest.raises(ProtectedWorkspaceUnavailable):
            with protected_workspace("native-check", base=base):
                pytest.fail("an unproven host mount must not be entered")
    assert list(base.iterdir()) == []


def _writable_native_ram_mount() -> Path | None:
    for candidate in (Path("/dev/shm"), Path(f"/run/user/{os.getuid()}")):
        if candidate.is_dir() and os.access(candidate, os.W_OK | os.X_OK):
            if ram_only_directory_proof(candidate).proven:
                return candidate
    return None


@pytest.mark.platforms("linux")
def test_native_success_on_a_proven_ram_mount() -> None:
    base = _writable_native_ram_mount()
    if base is None:
        pytest.skip("no writable RAM-only mount is proven on this host (swap active or unknown)")
    with protected_workspace("native-check", base=base) as workspace:
        _assert_owner_only_workspace(workspace, base, "native-check")
        (workspace / "scratch.bin").write_bytes(b"synthetic")
    assert not workspace.exists()


def _configure(monkeypatch, security) -> None:
    """Serve the effective config through the seam production reads."""
    from hermes_cli import config_effective

    layer = {} if security is None else {"security": security}
    monkeypatch.setattr(config_effective, "load_user_config_effective", lambda **_kw: dict(layer))


def test_configured_base_wins_over_the_runtime_dir(tmp_path: Path, base: Path, monkeypatch) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    _configure(monkeypatch, {"protected_work_dir": str(base)})
    assert protected_workspace_base() == base
    with protected_workspace("goal-verification", prove=_always_proven) as workspace:
        _assert_owner_only_workspace(workspace, base, "goal-verification")
    assert list(runtime.iterdir()) == []


@pytest.mark.parametrize("configured", ["", "relative/dir", 7, ["/run/user"]])
def test_invalid_configured_base_never_falls_back(tmp_path: Path, monkeypatch, configured) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    _configure(monkeypatch, {"protected_work_dir": configured})
    with pytest.raises(ProtectedWorkspaceUnavailable, match="protected_work_dir"):
        with protected_workspace("goal-verification", prove=_always_proven):
            pytest.fail("an invalid configured base must not fall back to the runtime directory")
    assert list(runtime.iterdir()) == []


def test_unproven_configured_base_never_falls_back(tmp_path: Path, base: Path, monkeypatch) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    _configure(monkeypatch, {"protected_work_dir": str(base)})

    def runtime_only(path: Path) -> RamProof:
        return _always_proven(path) if str(path).startswith(os.path.realpath(runtime)) else _never_proven(path)

    with pytest.raises(ProtectedWorkspaceUnavailable, match="active swap"):
        with protected_workspace("goal-verification", prove=runtime_only):
            pytest.fail("an unproven configured base must not fall back to the runtime directory")
    assert list(base.iterdir()) == [] and list(runtime.iterdir()) == []


@pytest.mark.parametrize("security", [None, {}, {"protected_work_dir": None}])
def test_absent_config_uses_the_runtime_dir_candidate_with_the_same_proof(base: Path, monkeypatch, security) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(base))
    _configure(monkeypatch, security)
    assert protected_workspace_status(prove=_never_proven) == _never_proven(base)
    with pytest.raises(ProtectedWorkspaceUnavailable):
        with protected_workspace("managed-codex", prove=_never_proven):
            pytest.fail("an unproven runtime directory must not be entered")
    with protected_workspace("managed-codex", prove=_always_proven) as workspace:
        _assert_owner_only_workspace(workspace, base, "managed-codex")
    assert list(base.iterdir()) == []


@pytest.mark.parametrize("runtime", [None, "", "relative/run"])
def test_no_config_and_no_runtime_dir_is_unavailable(monkeypatch, runtime) -> None:
    if runtime is None:
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    else:
        monkeypatch.setenv("XDG_RUNTIME_DIR", runtime)
    _configure(monkeypatch, None)
    with pytest.raises(ProtectedWorkspaceUnavailable, match="security.protected_work_dir"):
        protected_workspace_base()
