"""Shared helpers for attaching Hermes to a local Chromium-family CDP port.

Resolves the default Chromium browser + real profile dir, snapshots that profile for the
``browser.use_real_profile`` consent path, and discovers/launches a debug browser on a
loopback CDP port (dual-stack: IPv4 first, then IPv6).
"""

from __future__ import annotations

import contextlib
import json
import logging
import ntpath
import os
import platform
import posixpath
import re
import shlex
import shutil
import socket
import sqlite3
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from agent.proxy_bypass import is_loopback_host

logger = logging.getLogger(__name__)

DEFAULT_BROWSER_CDP_PORT = 9222
DEFAULT_BROWSER_CDP_URL = f"http://127.0.0.1:{DEFAULT_BROWSER_CDP_PORT}"


@dataclass(frozen=True)
class _Browser:
    """Per-browser install/profile locations for one Chromium-family product."""
    key: str
    mac_app: str
    mac_support: tuple[str, ...]          # under ~/Library/Application Support
    win_bins: tuple[str, ...]             # shutil.which names on Windows
    win_install: tuple[tuple[str, ...], ...]  # under Program Files / LOCALAPPDATA
    win_profile: tuple[str, ...]          # under LOCALAPPDATA
    linux_bins: tuple[str, ...]           # shutil.which names on Linux
    linux_paths: tuple[str, ...]          # known absolute install paths
    linux_config: str                     # under $XDG_CONFIG_HOME
    # PATH names tried by chromium_executable() when they differ from linux_bins
    # (channel/alias binaries are launch candidates only).
    linux_exec: tuple[str, ...] | None = None


# Launch-candidate order (chrome, chromium, brave, brave-origin, edge) is the tuple
# order. ``brave-origin`` is Brave's standalone paid build: same Chromium core but a
# fully distinct install identity (Brave-Origin product path, ``BraveOHTML`` ProgId,
# ``com.brave.Browser.origin`` bundle id) that installs side-by-side with Brave. Its
# profile is NOT under Brave-Browser and must never be conflated with the ``brave``
# key — a "brave" lookup resolving to the Origin binary drives the wrong profile.
# Positional layout per entry: key, mac_app / mac_support, win_bins / win_install /
# win_profile / linux_bins / linux_paths / linux_config [, linux_exec].
_BROWSERS = (
    _Browser(
        "chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ("Google", "Chrome"), ("chrome.exe", "chrome"),
        (("Google", "Chrome", "Application", "chrome.exe"),),
        ("Google", "Chrome", "User Data"),
        ("google-chrome", "google-chrome-stable"),
        ("/opt/google/chrome/chrome", "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable"),
        "google-chrome"),
    _Browser(
        "chromium", "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ("Chromium",), ("chromium.exe", "chromium"),
        (("Chromium", "Application", "chrome.exe"), ("Chromium", "Application", "chromium.exe")),
        ("Chromium", "User Data"),
        ("chromium-browser", "chromium"),
        ("/usr/bin/chromium-browser", "/usr/bin/chromium"),
        "chromium"),
    _Browser(
        "brave", "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        ("BraveSoftware", "Brave-Browser"), ("brave.exe", "brave"),
        (("BraveSoftware", "Brave-Browser", "Application", "brave.exe"),),
        ("BraveSoftware", "Brave-Browser", "User Data"),
        ("brave-browser", "brave-browser-stable", "brave"),
        ("/usr/bin/brave-browser", "/usr/bin/brave-browser-stable", "/usr/bin/brave",
         "/snap/bin/brave", "/opt/brave.com/brave/brave-browser", "/opt/brave.com/brave/brave",
         "/opt/brave-bin/brave"),
        "BraveSoftware/Brave-Browser"),
    _Browser(
        "brave-origin", "/Applications/Brave Origin.app/Contents/MacOS/Brave Origin",
        ("BraveSoftware", "Brave-Origin"), ("brave-origin.exe", "brave-origin"),
        (("BraveSoftware", "Brave-Origin", "Application", "brave.exe"),
         ("BraveSoftware", "Brave-Origin", "Application", "brave-origin.exe")),
        ("BraveSoftware", "Brave-Origin", "User Data"),
        ("brave-origin", "brave-origin-nightly"),
        ("/usr/bin/brave-origin", "/opt/brave.com/brave-origin/brave-origin",
         "/opt/brave.com/brave-origin-nightly/brave-origin"),
        "BraveSoftware/Brave-Origin", linux_exec=("brave-origin",)),
    _Browser(
        "edge", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ("Microsoft Edge",), ("msedge.exe", "msedge"),
        (("Microsoft", "Edge", "Application", "msedge.exe"),),
        ("Microsoft", "Edge", "User Data"),
        ("microsoft-edge", "microsoft-edge-stable", "msedge"),
        ("/usr/bin/microsoft-edge", "/usr/bin/microsoft-edge-stable",
         "/opt/microsoft/msedge/microsoft-edge", "/opt/microsoft/msedge/msedge"),
        "microsoft-edge", linux_exec=("microsoft-edge", "microsoft-edge-stable")),
)
_BROWSER_BY_KEY = {b.key: b for b in _BROWSERS}


# --- Default-Chromium resolution (``browser.use_real_profile``) ------------------------
# Only Chromium-family browsers are supported; a non-Chromium default (Firefox, Safari)
# resolves to None and the caller fails closed. Each platform table is a STABLE map plus a
# CHANNEL list: Beta/Dev/Canary are recognized but unsupported (their profiles live in dirs
# the tables don't carry) and are matched FIRST so they fail closed instead of being
# swallowed into the stable family — driving the wrong profile is a wrong-principal bug
# (#95549 invariant).

# Windows UserChoice ProgId prefixes → key. Case-insensitive PREFIX match so
# version suffixes (``ChromeHTML.X``) still resolve.
_WINDOWS_PROGID_MAP = (
    ("chromehtml", "chrome"), ("msedgehtm", "edge"),
    ("braveohtml", "brave-origin"),  # Brave Origin stable (brave-core install_static)
    ("bravehtml", "brave"), ("chromiumhtm", "chromium"))

# ``ChromeBHTML`` = Beta, ``ChromeDHTML`` = Dev, ``ChromeSSHTML`` = Canary (SxS);
# ``MSEdge[BDC]HTML`` = Edge channels; Brave Origin Beta=BraveOBHTML, Dev=BraveODHTML,
# Nightly/SxS=BraveOSHTM (no trailing L — 10-char cap).
_WINDOWS_CHANNEL_PROGIDS = (
    "chromebhtml", "chromedhtml", "chromesshtml", "chromecanaryhtml",
    "msedgebhtml", "msedgedhtml", "msedgechtml",
    "bravebetahtml", "bravenightlyhtml",
    "braveobhtml", "braveodhtml", "braveoshtm")

# Linux xdg default-web-browser .desktop name fragments → key (SUBSTRING match),
# including Flatpak application ids (``com.google.Chrome.desktop``).
_LINUX_DESKTOP_MAP = (
    ("google-chrome", "chrome"), ("com.google.chrome", "chrome"), ("chromium", "chromium"),
    # ORDER MATTERS: ``brave-origin.desktop`` contains the bare ``brave`` fragment,
    # so the substring scan must hit the Origin entry first (#95549).
    ("brave-origin", "brave-origin"), ("brave", "brave"),
    ("microsoft-edge", "edge"), ("com.microsoft.edge", "edge"), ("msedge", "edge"))

_LINUX_CHANNEL_FRAGMENTS = (
    "google-chrome-beta", "google-chrome-unstable", "google-chrome-canary",
    "com.google.chrome.beta", "com.google.chrome.dev", "com.google.chrome.canary",
    "microsoft-edge-beta", "microsoft-edge-dev", "microsoft-edge-canary",
    "brave-browser-beta", "brave-browser-nightly", "brave-browser-dev",
    "brave-origin-beta", "brave-origin-nightly", "brave-origin-dev")

# Where sandboxed Linux packages keep the profile instead of $XDG_CONFIG_HOME.
_LINUX_FLATPAK_IDS = {"chrome": "com.google.Chrome", "chromium": "org.chromium.Chromium",
                      "brave": "com.brave.Browser", "edge": "com.microsoft.Edge"}
_LINUX_SNAP_PROFILE_PARTS = {
    "chromium": ("snap", "chromium", "common", "chromium"),
    "brave": ("snap", "brave", "current", ".config", "BraveSoftware", "Brave-Browser")}

# macOS LaunchServices bundle-id → key. EXACT match (not prefix): ``com.google.chrome.beta``
# must not be read as ``com.google.chrome``, nor ``com.brave.browser.origin`` (Homebrew
# cask id for Brave Origin) as plain ``com.brave.browser``.
_DARWIN_BUNDLE_MAP = (
    ("com.google.chrome", "chrome"), ("com.microsoft.edgemac", "edge"),
    ("com.brave.browser", "brave"), ("com.brave.browser.origin", "brave-origin"),
    ("org.chromium.chromium", "chromium"))

_DARWIN_CHANNEL_BUNDLES = (
    "com.google.chrome.beta", "com.google.chrome.dev", "com.google.chrome.canary",
    "com.microsoft.edgemac.beta", "com.microsoft.edgemac.dev", "com.microsoft.edgemac.canary",
    "com.brave.browser.beta", "com.brave.browser.nightly",
    "com.brave.browser.origin.beta", "com.brave.browser.origin.dev",
    "com.brave.browser.origin.nightly")

# Sentinel for a recognized-but-unsupported Chromium CHANNEL default. Distinct from
# None (non-Chromium) so the caller can give a channel-specific message.
UNSUPPORTED_CHANNEL = "__unsupported_channel__"


def real_profile_data_dir(browser: str, system: str | None = None) -> str | None:
    """Default user-data-dir for ``browser`` on ``system`` (None if unknown). Linux tries native
    ($XDG_CONFIG_HOME), snap and Flatpak — first existing wins, else native so the caller's
    error names it. Darwin/Windows paths are not stat'ed."""
    b = _BROWSER_BY_KEY.get(browser)
    if b is None:
        return None
    system = system or platform.system()
    home = os.path.expanduser("~")
    if system == "Darwin":
        return posixpath.join(home, "Library", "Application Support", *b.mac_support)
    if system == "Windows":
        local = os.environ.get("LOCALAPPDATA") or ntpath.join(home, "AppData", "Local")
        return ntpath.join(local, *b.win_profile)
    config = os.environ.get("XDG_CONFIG_HOME") or posixpath.join(home, ".config")
    linux_parts = b.linux_config.split("/")
    candidates = [posixpath.join(config, *linux_parts)]
    if browser in _LINUX_SNAP_PROFILE_PARTS:
        candidates.append(posixpath.join(home, *_LINUX_SNAP_PROFILE_PARTS[browser]))
    if browser in _LINUX_FLATPAK_IDS:
        candidates.append(posixpath.join(home, ".var", "app", _LINUX_FLATPAK_IDS[browser], "config",
                                         *linux_parts))
    return next((c for c in candidates if os.path.isdir(c)), candidates[0])


def _first_present(paths) -> str | None:
    return next((p for p in paths if p and os.path.isfile(p)), None)


def chromium_executable(browser: str, system: str | None = None) -> str | None:
    """Return the first present executable for a Chromium ``browser``."""
    b = _BROWSER_BY_KEY.get(browser)
    if b is None:
        return None
    system = system or platform.system()
    if system == "Darwin":
        return _first_present((b.mac_app,))
    if system == "Windows":
        bases = [
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))]
        return _first_present(os.path.join(base, *parts) for base in bases for parts in b.win_install)
    # Linux: PATH lookup first, then the known absolute install paths.
    found = next(filter(None, map(shutil.which, b.linux_exec or b.linux_bins)), None)
    return found or _first_present(b.linux_paths)


def _classify_default(value: str, channels, table, match) -> str | None:
    """Map an OS default-browser identifier to a canonical key. Channels are checked FIRST: a
    Beta/Dev/Canary id must fail closed (UNSUPPORTED_CHANNEL), never match the stable profile."""
    if any(match(value, chan) for chan in channels):
        return UNSUPPORTED_CHANNEL
    return next((browser for frag, browser in table if match(value, frag)), None)


_WINDOWS_HTTPS_ASSOC = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https"


def _windows_shell_progid(scheme: str) -> str | None:
    """Effective ProgId for a URL scheme as the Windows Shell resolves it."""
    try:
        import ctypes
        from ctypes import wintypes

        query = ctypes.WinDLL("shlwapi", use_last_error=True).AssocQueryStringW
        query.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                          wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        query.restype = ctypes.c_long
        buf = ctypes.create_unicode_buffer(256)
        size = wintypes.DWORD(len(buf))
        # 20 = ASSOCSTR_PROGID. ASSOCSTR_EXECUTABLE can fail (0x80070483) on 25H2.
        if query(0, 20, scheme, None, buf, ctypes.byref(size)) != 0:
            return None
        return buf.value or None
    except (AttributeError, OSError):  # non-Windows host
        return None


def _windows_registry_progid() -> str | None:
    """Registry fallback. Windows 11 25H2 Settings writes only ``UserChoiceLatest\\ProgId``;
    the legacy ``UserChoice`` key can be stale, missing, or present without a value."""
    try:
        import winreg  # type: ignore
    except ImportError:
        return None
    for sub in (r"UserChoiceLatest\ProgId", "UserChoice"):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{_WINDOWS_HTTPS_ASSOC}\{sub}") as key:
                prog_id, _ = winreg.QueryValueEx(key, "ProgId")
        except OSError:
            continue
        if prog_id:
            return str(prog_id)
    return None


def _detect_default_windows() -> str | None:
    prog_id = _windows_shell_progid("https") or _windows_registry_progid()
    if not prog_id:
        return None
    return _classify_default(prog_id.lower(), _WINDOWS_CHANNEL_PROGIDS,
                             _WINDOWS_PROGID_MAP, str.startswith)


def _run_stdout(argv: list[str]) -> str | None:
    try:
        return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=5, check=False).stdout
    except Exception:
        return None


def _launchservices_https_handler(dump: str) -> str | None:
    """Bundle id registered for ``https`` in ``defaults read … LSHandlers`` output (an array of
    ``{ … }`` dicts). Only the ``LSHandlerURLScheme = https`` entry counts — a browser
    registered for another scheme or a file type must not be mistaken for the default."""
    entries: list[str] = []
    depth, buf = 0, []
    for ch in dump:
        depth += (ch == "{") - (ch == "}")
        if ch == "{" and depth == 1:
            buf = []
        elif ch == "}" and depth == 0:
            entries.append("".join(buf))
        elif depth >= 1:
            buf.append(ch)
    for entry in entries:
        low = entry.lower()
        if not re.search(r'lshandlerurlscheme\s*=\s*"?https"?\s*;', low):
            continue
        # Strip the nested LSHandlerPreferredVersions block first: on macOS 26 it carries
        # a VERSION NUMBER (LSHandlerRoleAll = "7559.97";), not the "-" placeholder older
        # releases used, and the role regex below would return it instead of the bundle id.
        # Left in, the role regex below would match that version before the real bundle id sitting at the
        # entry's own level and return "7559.97" — which maps to no browser, so detection fails on a machine
        # whose default IS Chrome (PR #95620 review).
        low = re.sub(r"lshandlerpreferredversions\s*=\s*\{[^}]*\}\s*;", "", low)
        # The real bundle id is the first non-"-" role value at this level.
        roles = re.findall(r'lshandlerrole(?:all|viewer)\s*=\s*"?([a-z0-9.\-]+)"?\s*;', low)
        return next((role for role in roles if role != "-"), None)
    return None


def _detect_default_darwin() -> str | None:
    out = _run_stdout(["defaults", "read",
                       "com.apple.LaunchServices/com.apple.launchservices.secure", "LSHandlers"])
    bundle = _launchservices_https_handler(out) if out is not None else None
    if not bundle:
        return None
    # Exact match. A non-Chromium https handler (Safari, Firefox, Arc, …) or an unknown
    # channel bundle fails closed: no "first installed Chromium wins" fallback.
    return _classify_default(bundle.lower(), _DARWIN_CHANNEL_BUNDLES, _DARWIN_BUNDLE_MAP, str.__eq__)


def _detect_default_linux() -> str | None:
    out = (_run_stdout(["xdg-settings", "get", "default-web-browser"]) or "").strip().lower()
    # Substring match; channels first because ``google-chrome-beta.desktop`` contains
    # the stable ``google-chrome`` fragment.
    return _classify_default(out, _LINUX_CHANNEL_FRAGMENTS, _LINUX_DESKTOP_MAP, str.__contains__)


def detect_default_chromium(system: str | None = None) -> str | None:
    """Return the canonical key of the default Chromium browser, or None."""
    detect = {"Windows": _detect_default_windows, "Darwin": _detect_default_darwin}
    return detect.get(system or platform.system(), _detect_default_linux)()


# --- Real-profile SNAPSHOT launch -------------------------------------------------------
# Never drive the live default user-data-dir: Chromium ≥136 (Google builds) refuses remote
# debugging on it, and the user's running browser holds it (SingletonLock). Instead snapshot
# the real profile into a Hermes-owned copy and launch the user's real binary on the copy —
# with NO mock-keychain/basic-store switches, so OS-keyring-encrypted cookies decrypt exactly
# as in the user's own browser. The copy is active only in proven RAM and persisted as an
# encrypted archive (``hermes_cli.browser_profile_store``); the source profile is only read.

# Excluded from the snapshot: caches/telemetry AND replay-prone state that hangs a fresh
# Chromium's renderer (extensions + service workers spin up on launch and wedge JS eval;
# IndexedDB/GPUCache add hundreds of MB). Only auth/login state is kept.
_SNAPSHOT_IGNORES = (
    "*Cache*",          # Cache, Code Cache, GPUCache, GrShaderCache, ShaderCache, GraphiteDawnCache, component_crx_cache, ...
    "Extensions",       # wallets/etc.: 100s of MB, and hang the renderer headless
    "Extension*",       # Extension State, Extension Rules, Extension Scripts
    "Local Extension Settings",
    "Service Worker",   # replays on launch → wedges the renderer
    "IndexedDB", "Crash Reports", "Crashpad", "BrowserMetrics*", "Snapshots",
    "OptimizationGuide*", "optimization_guide_model_store", "Safe Browsing", "SafetyTips",
    "OnDeviceHeadSuggestModel", "segmentation_platform", "Sync Data", "Shared Dictionary",
    "History*",         # large; not needed for auth
    "Favicons*",
    "Singleton*",       # live-instance symlinks; never valid in a copy
    "RunningChromeVersion", "SingletonSocket", "*.tmp",
    "*-journal",         # SQLite rollback journals — sidecars of the auth DBs,
    "*-wal",             # which are copied via online-backup; a stale sidecar
    "*-shm",             # next to a backed-up DB corrupts it.
    "BrowserMetrics-spare.pma",
)

# Auth-bearing files re-synced from the live profile on EVERY consented launch (the full
# tree is copied only when the snapshot doesn't exist yet). RELATIVE TO A PROFILE DIR; the
# caller mirrors them into the copy's ``Default``. No ``-journal``/``-wal`` sidecars: the
# DBs come via sqlite online-backup (committed state folded in); a stale journal corrupts.
_AUTH_REFRESH_PROFILE_FILES = (
    "Cookies", "Network/Cookies", "Login Data", "Login Data For Account", "Web Data", "Preferences")


def real_profile_copy_dir(browser: str) -> str:
    """The active RAM copy dir for ``browser``'s real profile. Pure: creates and decrypts nothing.

    Raises ``BrowserProfileError`` when no protected RAM base can be resolved."""
    from hermes_cli.browser_profile_store import owned_profile
    from hermes_security.browser_profile_archive import real_slot

    return str(owned_profile(real_slot(browser)).path)


def _account_identity(local_state: str, profile_key: str) -> dict:
    """The signed-in account recorded for ``profile_key`` (empty when signed out/unreadable)."""
    try:
        with open(local_state, encoding="utf-8-sig", errors="replace") as fh:
            entry = ((json.load(fh).get("profile") or {}).get("info_cache") or {}).get(profile_key) or {}
    except (OSError, ValueError, AttributeError):
        entry = {}
    entry = entry if isinstance(entry, dict) else {}
    return {"gaia_id": str(entry.get("gaia_id") or ""), "user_name": str(entry.get("user_name") or "")}


def real_profile_identity(browser: str, src: str, source_profile: str) -> dict:
    """Exact source identity a real-profile copy is bound to: browser, source dir, profile, account."""
    return {"browser": browser, "source_dir": os.path.realpath(src), "source_profile": source_profile,
            "account": _account_identity(os.path.join(src, "Local State"), source_profile)}


def legacy_copy_identity(browser: str, copy_dir: str) -> dict | None:
    """Best-known identity of a raw copy an older build left on disk (for explicit repair only)."""
    if browser not in _BROWSER_BY_KEY:
        return None
    try:
        with open(os.path.join(copy_dir, _SNAPSHOT_DONE_MARKER), encoding="utf-8") as fh:
            source_profile = fh.read().strip()
    except OSError:
        source_profile = ""
    src = real_profile_data_dir(browser)
    return {"browser": browser, "source_dir": os.path.realpath(src) if src else "",
            "source_profile": source_profile,
            # _sync_local_state rewrote the copy's info_cache to the source entry under "Default".
            "account": _account_identity(os.path.join(copy_dir, "Local State"), "Default")}


def _last_used_profile(src: str) -> str:
    """Profile dir Chrome last used (``Local State`` → profile.last_used), else ``Default``. The
    signed-in session usually lives in the profile actually browsed (``Profile 6``), not Default."""
    try:
        with open(os.path.join(src, "Local State"), encoding="utf-8-sig", errors="replace") as fh:
            state = json.load(fh)
        last = ((state.get("profile") or {}).get("last_used")) or "Default"
    except (OSError, ValueError, AttributeError):
        last = "Default"
    return last if isinstance(last, str) and os.path.isdir(os.path.join(src, last)) else "Default"


def _owner_only_tree(path: str) -> None:
    """Make every entry INSIDE the RAM copy owner-only (#96729: ``copy2`` keeps Chrome's 0644 and
    sqlite backups land umask-wide). The copy is this user's private RAM workspace, never a shared
    Hermes home, so no group-share carve-out applies. Symlinks are never followed."""
    for root, dirs, files in os.walk(path):
        for name, mode in ((d, 0o700) for d in dirs):
            if not os.path.islink(os.path.join(root, name)):
                os.chmod(os.path.join(root, name), mode)
        for name in files:
            if not os.path.islink(os.path.join(root, name)):
                os.chmod(os.path.join(root, name), 0o600)


# Auth files that are SQLite databases: on Windows a running Chrome holds these with an
# exclusive lock, so a raw copy raises WinError 32 and a best-effort skip leaves the copy
# signed-out. They are copied via SQLite's online-backup API instead. Matched by basename.
_SQLITE_AUTH_DBS = frozenset({"Cookies", "Login Data", "Login Data For Account", "Web Data"})
# Budget for one auth DB's online backup. A running browser holds Login Data / Login Data For
# Account / Web Data with a hot write lock, so backup makes no progress and this deadline is
# what fails — the reason users see, so it is a named constant rather than a bare string.
_AUTH_BACKUP_DEADLINE_S = 5.0
_AUTH_DB_LOCKED = ("SQLite backup made no progress within five seconds — "
                   "a running browser holds a write lock on it")


def _copy_auth_file(src_file: str, dst_file: str) -> str | None:
    """Copy auth state; returns None on success, else WHY the file could not be snapshotted.
    A DB that cannot be backed up consistently within the deadline is refused, never raw-copied."""
    os.makedirs(os.path.dirname(dst_file), exist_ok=True)
    try:
        if os.path.basename(src_file) in _SQLITE_AUTH_DBS:
            deadline = time.monotonic() + _AUTH_BACKUP_DEADLINE_S
            last_remaining: list[int | None] = [None]

            def check_deadline(_status: int, remaining: int, total: int) -> None:
                # A held write lock makes every step fail with ``remaining`` unchanged; a
                # large DB on a slow disk keeps shrinking it. Only the former is "locked" —
                # the all-locked message tells the user to quit the browser.
                before = total if last_remaining[0] is None else last_remaining[0]
                last_remaining[0] = remaining
                if _status != sqlite3.SQLITE_DONE and time.monotonic() >= deadline:
                    if remaining < before:
                        raise TimeoutError(f"SQLite backup exceeded {_AUTH_BACKUP_DEADLINE_S:g}s "
                                           "while still making progress")
                    raise TimeoutError(_AUTH_DB_LOCKED)

            # SQLite must coordinate both ends: immutable ignores committed source WAL,
            # while replacing only the destination file can replay its abandoned WAL.
            # Connection busy timeouts do not bound backup's retry loop; its callback does.
            with contextlib.closing(sqlite3.connect(
                    Path(src_file).resolve().as_uri() + "?mode=ro", uri=True, timeout=0.0)) as source:
                with contextlib.closing(sqlite3.connect(dst_file, timeout=0.0)) as out:
                    source.backup(out, pages=256, progress=check_deadline, sleep=0.1)
        else:
            shutil.copy2(src_file, dst_file)
        return None
    except (OSError, sqlite3.Error) as e:
        # A raw DB copy can lose committed WAL or overwrite a locked destination.
        logger.debug("real-profile: could not copy %s: %s", src_file, e)
        return str(e) or type(e).__name__


def _mirror_profile_auth(src: str, dst: str, source_profile: str) -> dict[str, str]:
    """Mirror ``source_profile``'s auth files into the copy's ``Default`` (agent-browser opens it);
    returns ``{relative name: reason}`` for the DB auth files that could NOT be copied ({} = clean)."""
    failed: dict[str, str] = {}
    for rel in _AUTH_REFRESH_PROFILE_FILES:
        s = os.path.join(src, source_profile, rel)
        if not os.path.isfile(s):
            continue
        reason = _copy_auth_file(s, os.path.join(dst, "Default", rel))
        if reason and os.path.basename(rel) in _SQLITE_AUTH_DBS:
            failed[rel] = reason
    return failed


def _unavailable_auth_dbs_error(browser: str, failed: dict[str, str]) -> str:
    """Fail-closed message naming WHICH auth databases could not be snapshotted and WHY. On
    macOS/Linux a running browser typically lets Cookies back up but holds Login Data / Login Data
    For Account / Web Data with a write lock, so the message must not read as "close the browser"
    when the real cause is an unreadable file (and vice versa)."""
    names = ", ".join(failed)
    if all(reason == _AUTH_DB_LOCKED for reason in failed.values()):
        return (f"{browser} is running and holds the profile's {names} with a write lock, so their "
                "SQLite backup made no progress within five seconds. Hermes does not fall back to a "
                "raw file copy (it could lose committed logins). Fully quit "
                f"{browser} (including any background instance) and retry, or turn "
                "browser.use_real_profile off.")
    details = "; ".join(f"{name}: {reason}" for name, reason in failed.items())
    return (f"could not read the '{browser}' profile's login data ({details}). "
            f"Close {browser} and retry, or turn browser.use_real_profile off.")


_SNAPSHOT_DONE_MARKER = ".hermes-snapshot-complete"
# Prefix stamped on the "profile is locked" error so the calling layer can recognize the
# needs-the-browser-closed condition and surface the close-with-approval flow.
_PROFILE_LOCKED_PREFIX = "[profile-locked] "


def _profile_is_locked(src: str, source_profile: str) -> bool:
    """True when the active profile's cookie DB can't be opened (browser running). On Windows a
    running browser holds Cookies deny-all (PermissionError); this FAST probe fails closed BEFORE
    the heavy snapshot so a locked profile never hangs the launch. Always False on POSIX."""
    db = _first_present(  # modern Network/ location first
        os.path.join(src, source_profile, rel)
        for rel in (os.path.join("Network", "Cookies"), "Cookies"))
    if not db:
        return False  # nothing to lock; let the copy path handle "no cookies"
    try:
        with open(db, "rb"):
            return False
    except OSError as e:  # other OSErrors are transient — don't declare locked; let the copy try
        return isinstance(e, PermissionError)


def _browser_setting(key: str):
    """Read ``browser.<key>`` from raw config; None when unset/unreadable."""
    try:
        from hermes_cli.config import read_raw_config
        browser_cfg = read_raw_config().get("browser", {})
        return browser_cfg.get(key) if isinstance(browser_cfg, dict) else None
    except Exception as e:
        logger.debug("could not read %s: %s", key, e)
        return None


def _real_profile_pin() -> str | None:
    """Pinned source profile dir name from ``browser.real_profile_pin`` (ALWAYS copied when set).
    Unset, the snapshot follows ``profile.last_used``, which can hand the agent the wrong identity.
    """
    pin = _browser_setting("real_profile_pin")
    return pin.strip() if isinstance(pin, str) and pin.strip() else None


def _resolve_source_profile(src: str) -> tuple[str | None, str | None]:
    """``(profile_dir_name, error)``: the pin if set, else last_used. A pin missing under ``src``
    FAILS CLOSED — falling back would silently browse as the wrong identity (wrong-principal)."""
    pin = _real_profile_pin()
    if pin:
        if os.path.isdir(os.path.join(src, pin)):
            return pin, None
        return None, (
            f"browser.real_profile_pin is set to '{pin}' but that profile directory does not "
            f"exist under {src!r}. Profile directories are named like 'Default' or 'Profile 2' "
            f"— list them with: ls {src!r}. Fix the pin, or remove it to fall back to the "
            "last-used profile.")
    return _last_used_profile(src), None


def _real_profile_autoclose() -> bool:
    """Whether browser.real_profile_autoclose consent is on (config read)."""
    return bool(_browser_setting("real_profile_autoclose") or False)


def _processes_holding_profile(src: str):
    """Yield psutil.Process instances holding ``src`` open: Chromium-family binaries whose
    cmdline references THIS user-data-dir — never an unrelated same-PID process. An unreadable
    cmdline is skipped."""
    try:
        import psutil
    except ImportError:  # hard dep; defensive
        return
    norm = os.path.normcase(os.path.normpath(src))
    browser_bins = (
        "chrome", "chrome.exe", "chromium", "chromium.exe", "chrome_crashpad",
        "brave", "brave.exe", "msedge", "msedge.exe", "google chrome")
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            cmd = proc.info.get("cmdline") or []
            joined = " ".join(cmd)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
        argv0 = cmd[0].lower() if cmd else ""  # some platforms report a generic name
        if not any(b in name or b in argv0 for b in browser_bins):
            continue
        # Binding: the exact user-data-dir must appear in the cmdline, normalized.
        if (norm in os.path.normcase(os.path.normpath(joined))
                or f"--user-data-dir={src}".lower() in joined.lower()):
            yield proc


def close_browser_holding_profile(src: str, timeout: float = 15.0) -> tuple[bool, str]:
    """Terminate the browser process tree holding ``src`` and wait for release. CONSENTED,
    DESTRUCTIVE (unsaved tab/form state is lost): only call after the user agreed."""
    try:
        import psutil
    except ImportError:
        return False, "psutil unavailable — cannot close the browser automatically."
    procs = list(_processes_holding_profile(src))
    if not procs:
        # Already closed, or the holder is a different user / unreadable — caller re-probes.
        return False, "no matching browser process found holding the profile."
    # Include child processes (renderers, GPU, crashpad) for a full tree kill.
    gone_errs = (psutil.NoSuchProcess, psutil.AccessDenied)
    targets = list(procs)
    for p in procs:
        with contextlib.suppress(*gone_errs):
            targets.extend(p.children(recursive=True))
    for p in targets:
        with contextlib.suppress(*gone_errs):
            p.terminate()
    alive = psutil.wait_procs(targets, timeout=min(timeout, 8.0))[1]
    for p in alive:
        with contextlib.suppress(*gone_errs):
            p.kill()
    psutil.wait_procs(alive, timeout=3.0)
    # The lock releases slightly after the process exits on Windows; poll.
    source_profile = _resolve_source_profile(src)[0] or _last_used_profile(src)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _profile_is_locked(src, source_profile):
            return True, "closed the browser and the profile lock released."
        time.sleep(0.5)
    return False, (
        "closed the browser processes but the profile is still locked — "
        "another instance may have relaunched (background/tray mode).")


def _sync_local_state(src: str, dst: str, source_profile: str) -> None:
    """Copy ``Local State`` into the snapshot and rewrite it for the single ``Default`` profile.
    Verbatim it names the SOURCE profile (last_used="Profile 2", info_cache of 2/4/7) the copy
    lacks, so Chrome would start SIGNED OUT. CRITICAL: Default's entry must be the SOURCE profile's
    identity (Default DIR holds its cookies), else Chrome demands "Continue as <name>" each launch.
    """
    ls_src, ls_dst = os.path.join(src, "Local State"), os.path.join(dst, "Local State")
    if os.path.isfile(ls_src):
        try:
            shutil.copy2(ls_src, ls_dst)
        except OSError as e:
            logger.debug("real-profile snapshot: skipped Local State: %s", e)
    try:
        with open(ls_dst, encoding="utf-8-sig") as fh:
            state = json.load(fh)
        prof = state.get("profile")
        if isinstance(prof, dict):
            cache = prof.get("info_cache")
            if isinstance(cache, dict):
                src_entry = cache.get(source_profile) or cache.get("Default")
                if src_entry:
                    prof["info_cache"] = {"Default": src_entry}
            prof["last_used"] = "Default"
            prof["last_active_profiles"] = ["Default"]
        with open(ls_dst, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
    except (OSError, ValueError) as e:
        logger.debug("real-profile snapshot: could not normalize Local State: %s", e)


def _locked_profile_error(browser: str) -> str:
    """Fail-closed message for a locked profile; offers auto-close only when consent arms it.
    NEVER kill from here: closing the browser is destructive and must be an explicit per-attempt,
    user-approved step. A still-locked retry blocks again — no auto-retry, no loop."""
    if _real_profile_autoclose():
        msg = (
            f"{browser} is running and has its profile locked, so its login data can't be copied "
            "yet. Hermes can close it for you (this quits the browser — you'll lose unsaved "
            "tabs). Ask the user to confirm, then close it and retry; if it's still locked after "
            "that, they must fully quit it (including any background/tray instance).")
    else:
        msg = (
            f"{browser} is running and has its profile locked, so its login data can't be copied. "
            "Fully quit the browser (including any background/tray instance) and retry, or turn "
            "browser.use_real_profile off. (Enable browser.real_profile_autoclose to let Hermes "
            "offer to close it for you.)")
    return _PROFILE_LOCKED_PREFIX + msg


def _copy_profile_tree(src: str, dst: str, source_profile: str) -> None:
    """Fresh (or torn-and-rebuilding) copy of the ACTIVE profile dir into the copy's Default,
    minus caches AND the SQLite auth DBs (raw copytree of a Chrome-held file raises on Windows);
    ``_mirror_profile_auth`` copies the DBs lock-aware instead."""
    dst_default = os.path.join(dst, "Default")
    try:
        shutil.rmtree(dst_default, ignore_errors=True)
        shutil.copytree(
            os.path.join(src, source_profile),
            dst_default,
            dirs_exist_ok=True,
            symlinks=False,
            ignore=shutil.ignore_patterns(*_SNAPSHOT_IGNORES, *_SQLITE_AUTH_DBS),
            ignore_dangling_symlinks=True)
    except shutil.Error as multi:
        # Per-file failures (browser mid-write) are non-fatal.
        logger.info(
            "real-profile snapshot: %d file(s) skipped copying %s/%s",
            len(multi.args[0]) if multi.args else 0, src, source_profile)


class _SnapshotRefused(Exception):
    """The copy refresh failed closed; the message is the user-facing reason."""


def _refresh_copy(browser: str, src: str, dst: str, source_profile: str) -> None:
    """Fill/refresh the RAM copy from the source. The completion marker is written only after full
    success, so a torn first copy (RAM full, Ctrl+C) never looks "already populated"."""
    marker = os.path.join(dst, _SNAPSHOT_DONE_MARKER)
    # Only a copy that previously COMPLETED counts as populated; a half-written tree is
    # rebuilt — otherwise a torn first copy poisons freshness forever.
    populated = os.path.isfile(marker)
    _sync_local_state(src, dst, source_profile)
    if not populated:
        _copy_profile_tree(src, dst, source_profile)
    # Both paths: lock-aware auth DB copy into Default — also the per-launch re-sync.
    failed_dbs = _mirror_profile_auth(src, dst, source_profile)
    if failed_dbs:  # even online-backup failed: never launch a silently signed-out session
        raise _SnapshotRefused(_unavailable_auth_dbs_error(browser, failed_dbs))
    # Never carry live-instance leftovers into the copy.
    for leftover in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        with contextlib.suppress(OSError):
            os.unlink(os.path.join(dst, leftover))
    # Mark complete only after everything above succeeded.
    try:
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write(source_profile)
    except OSError as e:
        logger.debug("real-profile snapshot: could not write done marker: %s", e)
    # AFTER the marker write so the marker itself is covered; every pass, so old copies heal.
    _owner_only_tree(dst)


def snapshot_real_profile(browser: str, src: str | None = None) -> tuple[str | None, str | None]:
    """Snapshot ``browser``'s real ACTIVE profile into the owned RAM copy; returns ``(dst, err)``.

    The copy is restored from (or created for) the encrypted archive bound to this exact source
    identity, then ``Local State`` plus the active profile's auth files are re-synced into its
    ``Default``. Persisting it back is the owned-profile lifecycle's job at a quiescent boundary."""
    from hermes_cli import browser_profile_store as store
    from hermes_security.browser_profile_archive import real_slot

    src = src or real_profile_data_dir(browser)
    if not src or not os.path.isdir(src):
        return None, (
            f"profile directory for '{browser}' was not found ({src!r}). "
            "Launch that browser at least once, or turn browser.use_real_profile off.")
    source_profile, resolve_err = _resolve_source_profile(src)
    if resolve_err or not source_profile:
        return None, resolve_err
    # Fast lock probe BEFORE any copy: a blocking file op on a Windows-locked cookie DB can
    # hang the launch for minutes. Never trips on POSIX; there a running browser surfaces later as
    # auth DB backups that miss their deadline (``_unavailable_auth_dbs_error``).
    if _profile_is_locked(src, source_profile):
        return None, _locked_profile_error(browser)
    try:
        profile = store.owned_profile(real_slot(browser))
        with store.prepare(profile, real_profile_identity(browser, src, source_profile)) as dst:
            _refresh_copy(browser, src, str(dst), source_profile)
    except (store.BrowserProfileError, _SnapshotRefused) as e:
        return None, str(e)
    except OSError as e:
        return None, f"could not snapshot the '{browser}' profile into its protected RAM copy: {e}"
    return str(dst), None


def cleanup_real_profile_snapshots() -> dict:
    """Consent is OFF: delete the real-profile archives and quiescent RAM copies (idempotent).

    A copy a browser still holds is retained and reported, never deleted under it; the debug
    profile archive and the operator's own browser profile are untouched."""
    from hermes_cli.browser_profile_store import discard_real_profiles

    report = discard_real_profiles([b.key for b in _BROWSERS])
    if report["removed"]:
        logger.info("real-profile: removed %s (consent off)", ", ".join(report["removed"]))
    if report["retained"]:
        logger.warning("real-profile: consent is off but kept %s", ", ".join(report["retained"]))
    return report


def _debug_candidate_paths(system: str):
    """Yield possible debug-browser binaries in launch order (may include None/missing)."""
    install_bases = (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                     os.environ.get("LOCALAPPDATA"))
    for b in _BROWSERS:
        if system == "Darwin":
            yield b.mac_app
        elif system == "Windows":
            yield from map(shutil.which, b.win_bins)
            for base in filter(None, install_bases):
                for parts in b.win_install:
                    yield os.path.join(base, *parts)
        else:
            yield from map(shutil.which, b.linux_bins)
            yield from b.linux_paths
    if system not in ("Darwin", "Windows"):
        # WSL: Windows installs under ``/mnt/c/...`` are POSIX paths regardless of the host
        # OS, so join with posixpath (os.path.join would emit backslashes on nt).
        for b in _BROWSERS:
            for base in ("/mnt/c/Program Files", "/mnt/c/Program Files (x86)"):
                for parts in b.win_install:
                    yield posixpath.join(base, *parts)


def get_chrome_debug_candidates(system: str) -> list[str]:
    candidates: dict[str, str] = {}  # normalized -> first path seen (dedupe, keep order)
    for path in filter(None, _debug_candidate_paths(system)):
        normalized = os.path.normcase(os.path.normpath(path))
        if normalized not in candidates and os.path.isfile(path):
            candidates[normalized] = path
    return list(candidates.values())


def chrome_debug_data_dir() -> str:
    """The active RAM copy of the ``/browser connect`` debug profile. Pure: creates nothing."""
    from hermes_cli.browser_profile_store import owned_profile
    from hermes_security.browser_profile_archive import DEBUG_SLOT

    return str(owned_profile(DEBUG_SLOT).path)


def _open_launch_stderr_log(path: str):
    """Open the launch stderr log owner-only (0600), truncating per candidate.

    The log has a fixed, guessable name inside the profile copy. The copy is this user's 0700 RAM
    workspace, but the mode is still set at creation and an existing inode is tightened after the
    truncating open and before any bytes are written, so it is never group/other-readable. A
    planted link is never followed."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchmod(fd, 0o600)
    except BaseException:
        os.close(fd)
        raise
    return os.fdopen(fd, "wb")


def _chrome_debug_args(port: int, data_dir: str) -> list[str]:
    return [f"--remote-debugging-port={port}", f"--user-data-dir={data_dir}",
            "--no-first-run", "--no-default-browser-check"]


def _tcp_open(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def is_browser_debug_ready(url: str, timeout: float = 1.0) -> bool:
    """Return True when ``url`` exposes a reachable Chrome DevTools endpoint."""
    parsed = urllib.parse.urlparse(url if "://" in url else f"http://{url}")
    try:
        port = parsed.port or (443 if parsed.scheme in {"https", "wss"} else 80)
    except ValueError:
        return False
    if parsed.scheme in {"ws", "wss"} and parsed.path.startswith("/devtools/browser/"):
        return bool(parsed.hostname) and _tcp_open(parsed.hostname, port, timeout)
    scheme = {"ws": "http", "wss": "https"}.get(parsed.scheme, parsed.scheme)
    if scheme not in {"http", "https"} or not parsed.netloc:
        return False
    root = f"{scheme}://{parsed.netloc}".rstrip("/")
    # Loopback readiness must not route through getproxies() (env or macOS system proxy, #110565).
    handlers = [urllib.request.ProxyHandler({})] if is_loopback_host(parsed.hostname) else []
    opener = urllib.request.build_opener(*handlers)
    for probe in (f"{root}/json/version", f"{root}/json"):
        try:
            with opener.open(probe, timeout=timeout) as resp:
                if 200 <= getattr(resp, "status", 200) < 300:
                    return True
        except Exception:
            continue
    return False


# Both loopback literals, IPv4 FIRST: Windows (and some Linux setups) can hand each loopback
# to a different process — Chrome asked to bind :9222 while VS Code's js-debug holds
# 127.0.0.1:9222 comes up on [::1]:9222 only, invisible to an IPv4-only probe.
_LOOPBACK_PROBE_HOSTS = ("127.0.0.1", "[::1]")
_LOOPBACK_SOCKET_HOSTS = ("127.0.0.1", "::1")


def discover_local_cdp_url(port: int, timeout: float = 1.0) -> str | None:
    """Return the first loopback URL (IPv4 first, then IPv6) speaking CDP, else None."""
    urls = (f"http://{host}:{port}" for host in _LOOPBACK_PROBE_HOSTS)
    return next((url for url in urls if is_browser_debug_ready(url, timeout=timeout)), None)


def local_port_in_use(port: int, timeout: float = 0.5) -> bool:
    """True when either loopback accepts TCP on ``port``. Used AFTER a failed CDP probe to tell
    "port is free, launch here" from "another application is squatting it; a launch would fight"."""
    return any(_tcp_open(host, port, timeout) for host in _LOOPBACK_SOCKET_HOSTS)


def find_free_debug_port(preferred: int = DEFAULT_BROWSER_CDP_PORT, attempts: int = 10) -> int:
    """First port after ``preferred`` bindable on both loopbacks; ``preferred + 1`` if none binds
    (the launch then fails with a clear browser-side error instead of silently doing nothing)."""
    def bindable(family, host, port) -> bool:
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((host, port))
            return True
        except OSError:
            return False
    loopbacks = ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1"))
    ports = range(preferred + 1, preferred + 1 + attempts)
    return next((p for p in ports if all(bindable(f, h, p) for f, h in loopbacks)), preferred + 1)


def manual_chrome_debug_command(port: int = DEFAULT_BROWSER_CDP_PORT, system: str | None = None,
                                data_dir: str | None = None) -> str | None:
    """A command the user can run by hand on the PREPARED debug profile copy (``data_dir`` from
    :class:`ChromeDebugLaunch`). None when no copy was prepared — never a path to an unprepared
    location, where a browser would create an unprotected profile."""
    if not data_dir:
        return None
    system = system or platform.system()
    candidates = get_chrome_debug_candidates(system)
    if candidates:
        argv = [candidates[0], *_chrome_debug_args(port, data_dir)]
        return subprocess.list2cmdline(argv) if system == "Windows" else shlex.join(argv)
    if system == "Darwin":
        return (f'open -a "Google Chrome" --args --remote-debugging-port={port} '
                f'--user-data-dir="{data_dir}" --no-first-run '
                "--no-default-browser-check")
    return None


def _detach_kwargs(system: str) -> dict:
    if system != "Windows":
        return {"start_new_session": True}
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    return {"creationflags": flags} if flags else {}


def _wait_for_browser_debug_ready_or_exit(
    proc: subprocess.Popen, port: int, timeout: float = 2.0, interval: float = 0.1) -> str:
    """Classify a launched browser as "ready", "exited" or "starting". The grace window only needs
    to catch a candidate that exits immediately; slower browsers may still finish starting later."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # Dual-stack: a squatter on the IPv4 loopback can push the browser to bind [::1] only.
        if discover_local_cdp_url(port, timeout=min(interval, 0.2)):
            return "ready"
        if proc.poll() is not None:
            return "exited"
        time.sleep(interval)
    return "starting"


_LAUNCH_STDERR_LOG = "launch-stderr.log"
_STDERR_TAIL_LIMIT = 2000


@dataclass
class LaunchAttempt:
    """Outcome of one candidate-binary launch attempt."""
    binary: str
    state: str  # "ready" | "starting" | "exited" | "spawn-failed"
    returncode: int | None = None
    stderr_tail: str = ""


@dataclass
class ChromeDebugLaunch:
    """Result of ``launch_chrome_debug``: ``launched`` = a browser was spawned and is ready or
    still starting (NOT a guarantee the CDP port ever opens); ``attempts`` explains *why* not.
    ``profile_dir`` is the prepared debug profile copy; ``profile_error`` says why none could be
    prepared (then nothing was launched and no manual command may be offered)."""
    launched: bool = False
    attempts: list[LaunchAttempt] = field(default_factory=list)
    profile_dir: str | None = None
    profile_error: str | None = None

    @property
    def hint(self) -> str | None:
        """Best user-facing explanation for a failed/soft launch, if any."""
        if self.profile_error:
            return self.profile_error
        for attempt in self.attempts:
            if attempt.state == "exited" and attempt.returncode == 0:
                name = os.path.basename(attempt.binary)
                return (
                    f"{name} exited immediately without opening the debug port — an already-running "
                    f"{name} instance likely absorbed the launch (Chromium's single-instance "
                    "behavior). Close ALL of its processes (including background/tray instances) "
                    "and retry /browser connect.")
        for attempt in self.attempts:
            if attempt.state == "exited" and attempt.stderr_tail:
                return (
                    f"{os.path.basename(attempt.binary)} exited before the debug port opened: "
                    f"{attempt.stderr_tail.splitlines()[-1].strip()}")
        return None


def _read_stderr_tail(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            return fh.read()[-_STDERR_TAIL_LIMIT:].decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def _launch_candidates(result: ChromeDebugLaunch, candidates: list[str], port: int, system: str,
                       profile) -> None:
    """Try each candidate on the prepared copy; the launched browser is bound to the copy so the
    owned-profile lifecycle saves it only after that browser has exited."""
    from hermes_cli.browser_profile_store import BrowserProfileError, record_launch

    stderr_path = os.path.join(result.profile_dir, _LAUNCH_STDERR_LOG)
    for candidate in candidates:
        try:
            with _open_launch_stderr_log(stderr_path) as stderr_file:
                proc = subprocess.Popen(
                    [candidate, *_chrome_debug_args(port, result.profile_dir)],
                    stdout=subprocess.DEVNULL, stderr=stderr_file, **_detach_kwargs(system))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            result.attempts.append(LaunchAttempt(binary=candidate, state="spawn-failed"))
            logger.info("browser debug launch: failed to spawn %s: %s", candidate, exc)
            continue
        try:
            record_launch(profile, proc)
        except (BrowserProfileError, OSError) as exc:  # its SingletonLock still marks the copy busy
            logger.warning("browser debug launch: browser not bound to its profile copy: %s", exc)
        logger.info(
            "browser debug launch: spawned %s (pid=%s) with --remote-debugging-port=%d",
            candidate, getattr(proc, "pid", None), port)
        state = _wait_for_browser_debug_ready_or_exit(proc, port)
        attempt = LaunchAttempt(binary=candidate, state=state)
        result.attempts.append(attempt)
        if state != "exited":
            result.launched = True
            return
        attempt.returncode = getattr(proc, "returncode", None)
        attempt.stderr_tail = _read_stderr_tail(stderr_path)
        logger.warning(
            "browser debug launch: %s exited (code=%s) before port %d opened%s",
            candidate, attempt.returncode, port,
            f"; stderr tail: {attempt.stderr_tail}" if attempt.stderr_tail else "")


def launch_chrome_debug(
    port: int = DEFAULT_BROWSER_CDP_PORT, system: str | None = None) -> ChromeDebugLaunch:
    """Launch a Chromium-family browser with remote debugging, trying each candidate in turn. One
    that exits before the CDP port opens (crash, singleton forward, bad profile dir) is logged with
    exit code + stderr tail and the next is tried.

    The browser runs on the debug profile's protected RAM copy, restored from its encrypted
    archive; a profile that cannot be prepared safely launches nothing (``profile_error``)."""
    from hermes_cli import browser_profile_store as store
    from hermes_security.browser_profile_archive import DEBUG_SLOT

    system = system or platform.system()
    result = ChromeDebugLaunch()
    candidates = get_chrome_debug_candidates(system)
    if not candidates:
        logger.info("browser debug launch: no Chromium-family binary found (system=%s)", system)
        return result
    try:
        profile = store.owned_profile(DEBUG_SLOT)
        with store.prepare(profile, {}) as data_dir:
            result.profile_dir = str(data_dir)
            _launch_candidates(result, candidates, port, system, profile)
    except store.BrowserProfileError as exc:
        result.profile_error = str(exc)
    return result
