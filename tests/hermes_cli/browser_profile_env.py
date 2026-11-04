"""Synthetic environment for owned browser-profile contracts (helper module, not tests).

A vaulted synthetic ``HERMES_HOME`` plus a RAM workspace base that the labelled unit seam
``hermes_cli.browser_profile_store._prove`` declares RAM-only. That seam is NOT evidence of a real
RAM mount; the native proof runs the live prover in the ``platforms("linux")`` contract.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

from hermes_platform.memory_protection import RamProof
from hermes_security import vault

PASSWORD = "synthetic owned browser profile password"
CANARY = b"synthetic-browser-cookie-canary-5c1e"


def owned_profile_env(tmp_path: Path, monkeypatch, *, proven: bool = True,
                      vaulted: bool = True) -> tuple[Path, Path]:
    """``(home, ram_base)``: an unlocked vaulted home (or a pre-migration one) and a RAM base
    proven via the unit seam."""
    home = tmp_path / "owner-home"
    home.mkdir()
    ram = tmp_path / "ram"
    ram.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(ram))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    if vaulted:
        vault.init_vault(home, PASSWORD)
        vault.unlock(home, PASSWORD)
    proof = RamProof(proven, "ramfs" if proven else "ext4", "synthetic unit proof")
    monkeypatch.setattr("hermes_cli.browser_profile_store._prove", lambda _path: proof)
    return home, ram


def reset_owned_profiles() -> None:
    from hermes_cli import browser_profile_store

    browser_profile_store._LEASES.clear()
    vault.clear_vault_cache()


def cookie_db(path: Path, value: str | None = None) -> str:
    """Store/read a marker row in a real SQLite DB (the shape of Chromium's Cookies store)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn, conn:
        if value is not None:
            conn.execute("create table if not exists cookies(value)")
            conn.execute("delete from cookies")
            conn.execute("insert into cookies values(?)", (value,))
        return conn.execute("select value from cookies").fetchone()[0]


def chromium_tree(root: Path, cookie: str = CANARY.decode(), *, stale_lock: bool = True) -> Path:
    """A synthetic Chromium user-data dir: state that must persist plus caches and a live-instance
    marker that must not (a ``SingletonLock`` left by a browser on another, unreachable host)."""
    cookie_db(root / "Default" / "Network" / "Cookies", cookie)
    storage = root / "Default" / "Local Storage" / "leveldb"
    storage.mkdir(parents=True)
    (storage / "000003.log").write_bytes(b"site-storage:" + cookie.encode())
    (root / "Default" / "Preferences").write_text(json.dumps({"profile": {"name": "Synthetic"}}))
    (root / "Local State").write_text(json.dumps({"profile": {"last_used": "Default"}}))
    cache = root / "Default" / "Cache" / "Cache_Data"
    cache.mkdir(parents=True)
    (cache / "data_0").write_bytes(b"regenerable-cache")
    if stale_lock:
        os.symlink("synthetic-host-1", root / "SingletonLock")
    return root


def plaintext_hits(root: Path, needle: bytes = CANARY) -> list[Path]:
    return [p for p in Path(root).rglob("*")
            if p.is_file() and not p.is_symlink() and needle in p.read_bytes()]


def exited_browser() -> subprocess.Popen:
    """A real local child that has already exited: stands in for a closed browser process."""
    child = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL)
    child.wait(timeout=30)
    return child


def application_state_tree(root: Path) -> dict[str, bytes]:
    """Independent payload oracle: cache-shaped names may hold the only offline app copy."""
    names = (
        "Default/Service Worker/CacheStorage/fixture/only-copy",
        "Default/Service Worker/ScriptCache/offline-worker",
        "Default/AppCache/only-copy",
        "Default/Local Storage/leveldb/Cache-manifest",
        "Default/Local Storage/leveldb/pending.tmp",
        "Default/app-data/Cache/only-copy",
        "Default/app-data/Crashpad/only-copy",
        "Default/app-data/DevToolsActivePort",
        "Default/app-data/SingletonLock",
        "Profile 2/Service Worker/CacheStorage/fixture/only-copy",
        "Custom Profile/Cache/only-copy",
        "CacheStorage/root-app-data",
        "MyCache/only-copy.tmp",
        "SingletonApplicationState/only-copy",
        "operator-state.tmp",
        "pending-app.tmp/only-copy",
        "BrowserMetrics-operator-state.tmp",
        "Profile 3/Cache",
    )
    payloads = {name: b"synthetic-private-only-copy|" + name.encode() for name in names}
    for name, payload in payloads.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    return payloads
