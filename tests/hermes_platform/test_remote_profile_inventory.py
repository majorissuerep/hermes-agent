"""Fixed native filesystem contracts; no browser/backend/Hermes app is executed."""
from contextlib import ExitStack
import hashlib
import os
import socket
import subprocess
import sys

import pytest

from hermes_platform.remote_profile import command

pytestmark = pytest.mark.platforms("linux")


def _run(operation, root, *, expected=""):
    argv = command(operation, str(root), maximum=1024**2, expected=expected)
    argv[0] = sys.executable
    env = {"PATH": "/usr/bin:/bin", "HOME": str(root.parent), "LANG": "C.UTF-8"}
    child = subprocess.Popen(argv, env=env, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        output, error = child.stdout.read(), child.stderr.read()
        return child.wait(timeout=15), output, error
    finally:
        child.stdin.close()
        child.stdout.close()
        child.stderr.close()
        if child.poll() is None:
            child.kill()
            child.wait()


def _entry(path):
    info = path.lstat()
    data = path.read_bytes() if path.is_file() else b""
    return info.st_dev, info.st_ino, info.st_mode, hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize("kind", ["socket", "fifo"])
def test_new_unsupported_native_entry_refuses_before_every_cleanup_effect(tmp_path, monkeypatch, kind):
    root = tmp_path / "profile"
    root.mkdir(mode=0o700)
    saved = root / "saved-private-data"
    saved.write_bytes(os.urandom(32))
    code, original, _ = _run("export", root)
    assert code == 0
    expected = hashlib.sha256(original).hexdigest()
    monkeypatch.chdir(root)
    with ExitStack() as resources:
        unknown = root / "unknown-new"
        if kind == "socket":
            endpoint = resources.enter_context(socket.socket(socket.AF_UNIX))
            endpoint.bind(unknown.name)
        else:
            os.mkfifo(unknown, 0o600)
        before = {p.name: _entry(p) for p in root.iterdir()}
        code, _, error = _run("retire", root, expected=expected)
        assert {p.name: _entry(p) for p in root.iterdir()} == before
        assert code == 1
        assert b"unsupported or foreign native profile entry; every copy retained" in error
        code, _, error = _run("export", root)
        assert code == 1 and b"unsupported or foreign native profile entry" in error
        assert {p.name: _entry(p) for p in root.iterdir()} == before


def test_ordinary_profile_and_original_root_runtime_symlink_still_retire(tmp_path):
    root = tmp_path / "profile"
    root.mkdir(mode=0o700)
    (root / "Default").mkdir()
    (root / "Default/Cache").mkdir()
    (root / "Default/Cache/data").write_bytes(os.urandom(32))
    (root / "SingletonSocket").symlink_to("/nonexistent-original-runtime-socket")
    code, original, _ = _run("export", root)
    assert code == 0
    code, _, error = _run("retire", root, expected=hashlib.sha256(original).hexdigest())
    assert code == 0, error
    assert list(root.iterdir()) == []


def test_changed_later_entry_is_retained_after_an_earlier_valid_unlink(tmp_path):
    root = tmp_path / "profile"
    root.mkdir(mode=0o700)
    (root / "a").write_bytes(b"original a")
    (root / "b").write_bytes(b"original b")
    code, original, _ = _run("export", root)
    assert code == 0
    argv = command("retire", str(root), maximum=1024**2,
                   expected=hashlib.sha256(original).hexdigest())
    wrapper = r'''
import os,sys,shutil
assert shutil.rmtree.avoids_symlink_attacks
original=os.unlink
def unlink(name,*args,**kwargs):
    result=original(name,*args,**kwargs)
    if name=="a":
        descriptor=os.open("b",os.O_WRONLY|os.O_TRUNC|os.O_NOFOLLOW,dir_fd=kwargs["dir_fd"])
        with os.fdopen(descriptor,"wb") as changed:changed.write(b"new bytes never checkpointed")
    return result
os.unlink=unlink
program=sys.argv[1];sys.argv=["fixed-helper",*sys.argv[2:]]
exec(program,{"__name__":"__main__"})
'''
    process = subprocess.Popen([sys.executable, "-I", "-c", wrapper, argv[3], *argv[4:]],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    try:
        output, error = process.stdout.read(), process.stderr.read()
        code = process.wait(timeout=15)
    finally:
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()
        if process.poll() is None:
            process.kill()
            process.wait()
    assert not (root / "a").exists(), (code, error)  # One legitimate verified effect.
    assert (root / "b").read_bytes() == b"new bytes never checkpointed"
    assert code == 1 and not output
    assert b"native profile entry changed before cleanup; retained" in error
