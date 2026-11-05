"""Real fixed-helper effects must retain a directory detached from the selected profile.

Only inert Python/tar and synthetic directories execute. The controlled fence moves a
real opened directory just before recursive cleanup, without changing helper code.
"""
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.platforms("linux")


def _run(argv, home):
    child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE,
                             env={"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "C.UTF-8"})
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


def test_reparented_open_directory_retains_all_original_bytes_before_unlink(tmp_path):
    from hermes_platform.remote_profile import command

    root = tmp_path / "profile"
    root.mkdir(mode=0o700)
    original = root / "Default"
    original.mkdir(mode=0o700)
    payload = os.urandom(32)
    (original / "original-private-data").write_bytes(payload)
    exported = command("export", str(root), maximum=1024**2)
    exported[0] = sys.executable
    code, raw, error = _run(exported, tmp_path)
    assert code == 0, error
    argv = command("retire", str(root), maximum=1024**2,
                   expected=hashlib.sha256(raw).hexdigest())
    moved = tmp_path / "detached-original"
    wrapper = r'''
import os,sys
from pathlib import Path
root,moved,program=sys.argv[1:4]
real_open=os.open
def opened(name,*args,**kwargs):
    descriptor=real_open(name,*args,**kwargs)
    if name=="Default" and sys._getframe(1).f_code.co_name=="native_remove" and not Path(moved).exists():
        os.rename(name,moved,src_dir_fd=kwargs["dir_fd"])
        replacement=Path(root)/"Default"
        replacement.mkdir(mode=0o700)
        (replacement/"new-uncheckpointed-data").write_bytes(b"new bytes must stay")
    return descriptor
os.open=opened
sys.argv=["fixed-helper",*sys.argv[4:]]
exec(program,{"__name__":"__main__"})
'''
    code, output, error = _run([sys.executable, "-I", "-c", wrapper,
                                str(root), str(moved), argv[3], *argv[4:]], tmp_path)
    assert moved.is_dir(), "The controlled native directory-move fence was not reached"
    assert (moved / "original-private-data").read_bytes() == payload
    assert (root / "Default/new-uncheckpointed-data").read_bytes() == b"new bytes must stay"
    assert code == 1 and not output, error


def test_reparented_ancestor_of_open_nested_directory_retains_descendants(tmp_path):
    from hermes_platform.remote_profile import command

    root = tmp_path / "profile"
    root.mkdir(mode=0o700)
    nested = root / "Default/Cache"
    nested.mkdir(mode=0o700, parents=True)
    payload = os.urandom(32)
    other = os.urandom(32)
    (nested / "original-private-data").write_bytes(payload)
    (root / "Default/other-private-data").write_bytes(other)
    identities = {path.relative_to(root / "Default"): (path.stat().st_dev, path.stat().st_ino)
                  for path in (nested, nested / "original-private-data", root / "Default/other-private-data")}
    exported = command("export", str(root), maximum=1024**2)
    exported[0] = sys.executable
    code, raw, error = _run(exported, tmp_path)
    assert code == 0, error
    argv = command("retire", str(root), maximum=1024**2,
                   expected=hashlib.sha256(raw).hexdigest())
    moved = tmp_path / "detached-original-parent"
    wrapper = r'''
import os,sys
from pathlib import Path
root,moved,program=sys.argv[1:4]
real_open=os.open
def opened(name,*args,**kwargs):
    descriptor=real_open(name,*args,**kwargs)
    if name=="Cache" and sys._getframe(1).f_code.co_name=="native_remove" and not Path(moved).exists():
        original=Path(root)/"Default"
        original.rename(moved)
        replacement=Path(root)/"Default/Cache"
        replacement.mkdir(mode=0o700,parents=True)
        (replacement/"new-uncheckpointed-data").write_bytes(b"new bytes must stay")
    return descriptor
os.open=opened
sys.argv=["fixed-helper",*sys.argv[4:]]
exec(program,{"__name__":"__main__"})
'''
    code, output, error = _run([sys.executable, "-I", "-c", wrapper,
                                str(root), str(moved), argv[3], *argv[4:]], tmp_path)
    assert moved.is_dir(), "The controlled native ancestor-move fence was not reached"
    assert (moved / "Cache/original-private-data").read_bytes() == payload
    assert (moved / "other-private-data").read_bytes() == other
    assert all(((moved / relative).stat().st_dev, (moved / relative).stat().st_ino) == expected
               for relative, expected in identities.items())
    assert (root / "Default/Cache/new-uncheckpointed-data").read_bytes() == b"new bytes must stay"
    assert code == 1 and not output, error
