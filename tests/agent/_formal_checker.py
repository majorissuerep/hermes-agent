"""Declared PM/checker fixtures: real installed-state/tree checks, synthetic public executable.

No official Lean or RAM proof is claimed by these stand-ins. Native E2E uses real PM artifacts.
"""
from contextlib import contextmanager
from pathlib import Path
import sys
import tempfile


def install_checker(tmp_path, monkeypatch, body: str):
    from pm import paths
    from pm.lock import Facts, Lockfile
    from pm.registry import get_package
    from pm.store import current_target, tree_digest
    from agent import formal_logic_lean

    store = tmp_path / "fixture-pm-store"
    store.mkdir()
    monkeypatch.setenv("HERMES_RUNTIME_DIR", str(store))
    package, target = get_package("lean"), current_target()
    lock = Lockfile(paths.lockfile_path())
    version = lock.version("lean")
    entry = store / package.store_entry(version, target)
    binary = entry / "bin" / "lean"
    binary.parent.mkdir(parents=True)
    binary.write_text(f"#!{sys.executable}\n" + body)
    binary.chmod(0o700)
    library = entry / "lib" / "lean" / "Init.olean"
    library.parent.mkdir(parents=True)
    library.write_bytes(b"declared unit standard-library stand-in")
    Facts(store / "facts.json").record("lean", version, entry.name, {}, store, target=target,
        artifacts=[row["sha256"] for row in lock.artifacts("lean", target)], digest=tree_digest(entry))

    @contextmanager
    def scratch():
        with tempfile.TemporaryDirectory(dir=tmp_path, prefix="unit-checker-") as directory:
            path = Path(directory)
            path.chmod(0o700)
            yield path, "unit_directory_not_a_RAM_proof"

    monkeypatch.setattr(formal_logic_lean, "workspace", scratch)
    return binary
