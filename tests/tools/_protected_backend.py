"""A selected terminal backend whose exec transport is simulated from declared machine facts.

Only the transport is fake. Production code sends its real probe argv; this backend answers with a
report framed exactly as ``hermes_platform.remote_workspace`` frames it, using the
declared mount table, swap table, uid and directories. The real shell probe runs against a real
kernel in ``tests/hermes_platform/test_remote_workspace.py``'s native Linux cases.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

NO_SWAP = "Filename\tType\tSize\tUsed\tPriority\n"
ACTIVE_SWAP = NO_SWAP + "/swap.img                               file\t\t4194300\t0\t-2\n"


class FakeBackend:
    def __init__(self, mounts, *, swaps=NO_SWAP, uid="1000", temp="/tmp", xdg="", modes=None):
        self.mounts = list(mounts)  # (mount point, fstype, super options)
        self.swaps, self.uid, self.temp, self.xdg = swaps, uid, temp, xdg
        self.modes = {"/tmp": "1777", **(modes or {})}
        self.dirs: dict[str, str] = {}  # backend path -> "uid:mode"
        self.links: dict[str, str] = {}  # backend path -> link target
        self.files: dict[str, bytes] = {}
        self.calls: list[list[str]] = []
        self.exported: list[dict] = []
        self.epoch = f"00000000-0000-0000-0000-000000000000:{id(self)}:1:1"
        self.native_ids: dict[str, str] = {}

    def get_temp_dir(self) -> str:
        return self.temp

    def mountinfo(self) -> str:
        return "".join(f"{20 + i} 1 0:{40 + i} / {point} rw,nosuid shared:{i} - {fstype} none {options}\n"
                       for i, (point, fstype, options) in enumerate([("/", "overlay", "rw"), *self.mounts]))

    def probes(self, mode: str) -> list[list[str]]:
        return [argv for argv in self.calls if argv[3:4] == ["hermes-protected-scratch"] and argv[5] == mode]

    def run_in(self, env, argv, *, child_env=None, timeout=30.0, **_kwargs):
        assert env is self, "a command was routed to another execution target"
        self.calls.append(list(argv))
        self.exported.append(dict(child_env or {}))
        if argv[:2] == ["sh", "-c"] and argv[3:4] == ["hermes-protected-scratch"]:
            return subprocess.CompletedProcess(argv, 0, self._report(argv), b"")
        if argv[:2] == ["sh", "-c"] and argv[3:4] == ["hermes-runtime-identity"]:
            fields = [self.epoch]
            for path in argv[4:]:
                if path not in self.dirs or path in self.links or self.dirs[path] != f"{self.uid}:700":
                    return subprocess.CompletedProcess(argv, 1, b"", b"")
                native = self.native_ids.setdefault(path, f"{id(self)}:{len(self.native_ids) + 1}")
                fields.append(f"{self.dirs[path]}:{native}")
            return subprocess.CompletedProcess(argv, 0, ("\0".join(fields) + "\0").encode(), b"")
        if argv[:2] == ["sh", "-c"] and argv[3:4] == ["hermes-browser-guard"]:
            count = int(argv[5])
            expected = dict(zip(argv[6:6 + 2 * count:2], argv[7:6 + 2 * count:2]))
            actual = {p: f"{self.dirs.get(p, '')}:{self.native_ids.get(p, '')}" for p in expected}
            if argv[4] != self.epoch or actual != expected or any(p in self.links for p in expected):
                return subprocess.CompletedProcess(argv, 97, b"", b"hermes: the protected browser runtime is missing or changed")
            return self.run_in(env, argv[6 + 2 * count:], **_kwargs)
        if argv[:3] == ["rm", "-f", "--"]:
            for path in argv[3:]:
                self.files.pop(path, None)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    def fetch_file(self, remote_path: str, local_dest: Path, *, max_bytes: int) -> None:
        from tools.environments.base import FileFetchError
        if remote_path not in self.files:
            raise FileFetchError(f"could not read {remote_path!r} in the sandbox")
        Path(local_dest).write_bytes(self.files[remote_path][:max_bytes])

    def _report(self, argv: list[str]) -> bytes:
        nonce, mode, args = argv[4], argv[5], argv[8:]
        out = [nonce, "uid", self.uid]
        if mode == "facts":
            for candidate in [self.xdg, *args]:
                physical = self.links.get(candidate, candidate) if candidate.startswith("/") else ""
                out += ["cand", candidate, physical, self.modes.get(physical, "700") if physical else ""]
        elif mode == "provision":
            out += self._provision(args[0], args[1:])
        else:
            out += self._verify(args[0], args[1:])
        out += ["mountinfo", self.mountinfo(), "swaps", self.swaps]
        return ("\0".join(out) + "\0").encode()

    def _provision(self, root: str, children: list[str]) -> list[str]:
        out, state = [], "ok"
        for child in ["", *children]:
            path = f"{root}/{child}" if child else root
            owner = self.dirs.setdefault(path, f"{self.uid}:700").split(":")[0] if path not in self.links else None
            if owner != self.uid:
                state = f"refused:{child}"
                break
            out += ["dir", child, path, self.dirs[path]]
        return out + ["state", state]

    def _verify(self, root: str, children: list[str]) -> list[str]:
        out = []
        for child in ["", *children]:
            path = f"{root}/{child}" if child else root
            owner_mode = self.dirs.get(path, "")
            if path in self.links or owner_mode.split(":")[0] != self.uid:
                return out + ["state", f"refused:{child}"]
            out += ["dir", child, path, owner_mode]
        return out + ["state", "ok"]
