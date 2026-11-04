"""Read-only PM selection and actual scratch posture for the existing formal consumer."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import re
import tempfile

from hermes_platform.checker_process import CheckerExecutable, capture_executable


@dataclass(frozen=True)
class InstalledChecker:
    executable: CheckerExecutable
    version: str
    archive_sha256: tuple[str, ...]
    tree_sha256: str
    target: str

    def receipt(self) -> dict:
        return {"package": "lean", "version": self.version, "target": self.target,
                "archive_sha256": list(self.archive_sha256), "tree_sha256": self.tree_sha256,
                "binary_sha256": self.executable.sha256, "pm_verified": True}


def installed_checker(requested: str = "lean", *, version: str = "4.35.0") -> InstalledChecker | None:
    """A legacy explicit path may name the selected PM binary; never trust PATH/elan or redirect it."""
    from pm import installed_package
    from pm.lock import Facts, Lockfile
    from pm.paths import lockfile_path
    from pm.store import current_target

    selected = installed_package("lean", verify=True)
    if selected is None or selected.binary is None:
        return None
    binary, root = selected.binary.resolve(strict=True), selected.path.resolve(strict=True)
    if requested != "lean" and (not Path(requested).is_absolute() or Path(requested).resolve() != binary):
        return None
    lock, target = Lockfile(lockfile_path()), current_target()
    artifacts = lock.artifacts("lean", target)
    if selected.version != version or lock.version("lean") != version or not artifacts:
        return None
    hashes = tuple(row.get("sha256", "") for row in artifacts)
    fact = Facts(selected.path.parent / "facts.json").get("lean") or {}
    tree = fact.get("digest", "")
    if (fact.get("target") != target or tuple(fact.get("artifacts", ())) != hashes
            or fact.get("version") != version or fact.get("entry") != selected.path.name):
        return None
    if not all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) for value in (*hashes, tree)):
        return None
    return InstalledChecker(capture_executable(binary, root=root), version, hashes, tree, target)


@contextmanager
def workspace():
    """Linux requires proven RAM; macOS preserves its stdin-only, empty-directory consumer.

    macOS has no RAM-only prover in this fork. Its explicit compatibility posture never
    claims RAM protection or whole-process swap/core confidentiality. Checker stdin and
    diagnostics stay in anonymous pipes and unexpected file creation prevents acceptance.
    """
    from hermes_platform.host.facts import os_family

    if os_family() == "linux":
        from hermes_security.protected_workspace import protected_workspace
        with protected_workspace("formal-checker") as directory:
            yield directory, "proven_ram_only"
    elif os_family() == "darwin":
        with tempfile.TemporaryDirectory(prefix="hermes-formal-checker-") as directory:
            path = Path(directory).resolve()
            os.chmod(path, 0o700)
            yield path, "empty_private_directory_stdin_only"
    else:
        raise RuntimeError("The formal checker requires native Linux or macOS")
