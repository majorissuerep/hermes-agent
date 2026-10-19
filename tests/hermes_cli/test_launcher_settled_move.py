"""A verified recovery closure distinguishes a completed move from an active owner."""
import hashlib
import re
import sys

import pytest

from hermes_cli import _early_recovery, _launchers


@pytest.mark.parametrize("settled", [True, False])
def test_verified_recovery_continues_only_after_marker_is_settled(tmp_path, settled, capsys):
    root = tmp_path / "checkout"
    git = root / ".git"
    git.mkdir(parents=True)
    pre = "a" * 40
    marker = git / _early_recovery.INTERRUPTED_PULL_MARKER
    marker.write_text(f"pre={pre}\n")
    closure = git / _early_recovery.RECOVERY_CLOSURE_DIR / pre
    blobs = {name: b"" for name in (*_early_recovery.RECOVERY_CLOSURE, _early_recovery.RECOVERY_CLOSURE_INIT)}
    action = f"(root / '.git' / {_early_recovery.INTERRUPTED_PULL_MARKER!r}).unlink()" if settled else "pass"
    blobs["hermes_cli/_early_recovery.py"] = (
        f"def restore_interrupted_pull(root):\n    {action}\n    return False\n"
    ).encode()
    lines = []
    for name, data in blobs.items():
        path = closure / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        oid = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
        lines.append(f"{oid} {name}\n")
    (closure / _early_recovery.RECOVERY_CLOSURE_MANIFEST).write_text("".join(lines))
    original_path = sys.path[:]
    modules = sys.modules.copy()
    namespace = {"sys": sys, "os": __import__("os"), "re": re}
    try:
        exec(_launchers._closure_repair_script(root), namespace)
        if settled:
            namespace["_hermes_closure_repair"]()
            assert not marker.exists()
            assert sys.path == original_path
            assert "hermes_cli" not in sys.modules
        else:
            with pytest.raises(SystemExit, match="1"):
                namespace["_hermes_closure_repair"]()
            assert marker.exists()
            assert "launch again once the update that owns it finishes" in capsys.readouterr().err
    finally:
        sys.path[:] = original_path
        for name in list(sys.modules):
            if name not in modules:
                del sys.modules[name]
        sys.modules.update(modules)
