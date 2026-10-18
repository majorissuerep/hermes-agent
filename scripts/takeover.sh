#!/usr/bin/env bash
# Fork takeover: replace an existing hermes-agent install with the
# majorissuerep/hermes-agent encrypted fork, preserving and migrating all
# sessions/configs onto the master-password vault.
#
# What it does:
#   1. Refuses active Hermes sessions/tasks; asks you to close them first.
#   2. Moves the old source checkout aside (kept, not deleted).
#   3. Clones the fork into the install location.
#   4. Prepares the pinned Python and dependencies through PM.
#   5. Repoints the `hermes` launcher at the fork.
#   6. Migrates ~/.hermes in place: full tar backup, then SQLCipher/envelope
#      conversion with verification (interactive: you pick the master password).
#
# Re-run safe: every step checks current state first.
set -euo pipefail

FORK_SSH="git@github.com:majorissuerep/hermes-agent.git"
FORK_HTTPS="https://github.com/majorissuerep/hermes-agent.git"
# Offline/airgapped installs: FORK_SRC may point at a local mirror
# (file:///path or /path) — origin is still pinned to the fork afterwards so
# updates track majorissuerep/hermes-agent.
FORK_SRC="${FORK_SRC:-$FORK_SSH}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SCRATCH_DIR="${TMPDIR:-$HERMES_HOME/cache/scratch}"
mkdir -p "$SCRATCH_DIR"
chmod 700 "$SCRATCH_DIR"
INSTALL_DIR="$HERMES_HOME/hermes-agent"
LAUNCHER="$HOME/.local/bin/hermes"
STAMP="$(date +%Y%m%d-%H%M%S)"
QUIET=0
[[ "${1:-}" == "-q" || "${1:-}" == "--quiet" ]] && QUIET=1

say() { [[ $QUIET -eq 1 ]] || echo "$@"; }
die() { echo "✗ $*" >&2; exit 1; }

command -v git >/dev/null || die "git not found"

# ── 1. Refuse live owners; never stop services or signal other processes ─────
require_quiescent_home() {
    local guard_python
    guard_python="$(command -v python3 || command -v python || true)"
    [[ -n "$guard_python" ]] || die "Python is required to verify that Hermes sessions are closed"
    "$guard_python" - "$HERMES_HOME" "$INSTALL_DIR" <<'PY_GUARD'
import fcntl
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

home, install = (Path(value).expanduser().resolve() for value in sys.argv[1:])
modules = {"hermes_cli.main", "gateway.run", "tui_runtime", "tui_runtime.entry",
           "tui_runtime.__main__", "tui_gateway", "tui_gateway.entry", "hermes_kernel_runner"}


def below(path, root):
    return path == root or root in path.parents


def owned_interpreter(path):
    return below(path, install) or below(path, home / "installs")


def runtime_command(argv, cwd):
    if not argv:
        return False
    executable = Path(argv[0])
    if not executable.is_absolute():
        executable = cwd / executable
    if owned_interpreter(executable.absolute()):
        return True  # Even arbitrary code in this install's interpreter can hold its databases.
    if not re.fullmatch(r"(?:python(?:w)?(?:\d+(?:\.\d+)*)?|pypy(?:\d+)?)", executable.name):
        return False
    args = iter(argv[1:])
    for token in args:
        if token == "-m":
            return next(args, "") in modules
        if token.startswith("-m"):
            return token[2:] in modules
        if token in {"-c", "-"}:
            return False  # Trailing strings are data, not process identity.
        if token in {"-W", "-X"}:
            next(args, None)
        elif token.startswith("-"):
            continue
        else:
            script = Path(token)
            return below((script if script.is_absolute() else cwd / script).resolve(), install)
    return False


def holds_state_database(entry):
    try:
        descriptors = list((entry / "fd").iterdir())
    except PermissionError:
        return False  # Protected unrelated processes (for example dbus) reveal no ownership evidence.
    for descriptor in descriptors:
        try:
            target = Path(os.readlink(descriptor).removesuffix(" (deleted)"))
            if target.name in {"state.db", "state.db-wal", "state.db-shm"} and below(target, home):
                return True
        except (FileNotFoundError, PermissionError):
            continue
    return False


def linux_owners():
    owners = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal() or int(entry.name) == os.getpid():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            argv = [part.decode(errors="surrogateescape") for part in (entry / "cmdline").read_bytes().split(b"\0") if part]
            if holds_state_database(entry):
                owners.append(entry.name)
                continue
            try:
                cwd = (entry / "cwd").resolve()
            except PermissionError:
                cwd = Path("/")
            if not runtime_command(argv, cwd):
                continue
            env = dict(part.split(b"=", 1) for part in (entry / "environ").read_bytes().split(b"\0") if b"=" in part)
            active = Path(os.fsdecode(env.get(b"HERMES_HOME", env.get(b"HOME", b"/nonexistent") + b"/.hermes"))).expanduser().resolve()
            if below(active, home):
                owners.append(entry.name)
        except (FileNotFoundError, ProcessLookupError):
            continue  # An exited process no longer holds state.
        except PermissionError:
            raise RuntimeError(f"Cannot verify process {entry.name}; close sessions and retry") from None
    return owners


def other_posix_owners():
    # macOS exposes argv through ps, not procfs. Without a trustworthy per-process home,
    # conservatively refuse exact runtime identities even when they may own another profile.
    listing = subprocess.check_output(["ps", "-ax", "-o", "uid=", "-o", "pid=", "-o", "command="], text=True)
    owners = []
    for row in listing.splitlines():
        fields = row.split(None, 2)
        if len(fields) == 3 and fields[0] == str(os.getuid()):
            if runtime_command(shlex.split(fields[2]), Path("/")):
                owners.append(fields[1])
    return owners


try:
    # Existing native/legacy locks catch inline launchers without interpreting Python source.
    for scope in (home, *(home / "profiles").glob("*")):
        for name in ("runtime.lock", "gateway.lock"):
            lock = scope / name
            if lock.exists():
                with lock.open("rb") as handle:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    owners = linux_owners() if Path("/proc/self/cmdline").is_file() else other_posix_owners()
    if owners:
        raise RuntimeError("Active Hermes processes: " + ", ".join(owners))
except (OSError, RuntimeError, ValueError) as error:
    print(f"Takeover refused: {error}. Close Hermes sessions/tasks before retrying; no processes were stopped.", file=sys.stderr)
    raise SystemExit(1)
PY_GUARD
}

# ── 2/3. Swap the source tree ───────────────────────────────────────────────
_bounded_git() {
    # Bounded wait without GNU coreutils: macOS ships no `timeout` command, and a
    # bare `timeout 60 ...` exits 127 there — takeover then "continued on the
    # existing checkout" and rebuilt STALE code (real incident: a Mac kept
    # resolving sqlcipher3-binary 0.6.0 from the pre-#10 pyproject).
    if command -v timeout >/dev/null 2>&1; then
        timeout 60 "$@"
    else
        "$@" &
        local _pid=$!
        ( sleep 60; kill -9 $_pid 2>/dev/null ) &
        local _watchdog=$!
        wait $_pid
        local _rc=$?
        kill -9 $_watchdog 2>/dev/null
        return $_rc
    fi
}

swap_source() {
    if [[ -d "$INSTALL_DIR/.git" ]]; then
        origin_url="$(git -C "$INSTALL_DIR" remote get-url origin 2>/dev/null || true)"
        if [[ "$origin_url" == *majorissuerep/hermes-agent* ]]; then
            say "→ Fork already checked out at $INSTALL_DIR; updating..."
            # Fetch failure must NEVER block a takeover whose tree is local:
            # bounded wait, then warn and continue on the existing checkout.
            if _bounded_git git -C "$INSTALL_DIR" fetch origin --quiet 2>/dev/null; then
                local_branch="$(git -C "$INSTALL_DIR" rev-parse --abbrev-ref HEAD)"
                git -C "$INSTALL_DIR" reset --hard "origin/$local_branch" --quiet
            else
                say "  ○ origin unreachable — continuing on the existing checkout"
            fi
            # Staleness guard: the sync below builds THIS tree; if it is behind
            # origin/main the user gets a loud, actionable message instead of a
            # silently stale install (the macOS sqlcipher3 incident).
            if git -C "$INSTALL_DIR" rev-parse --verify -q origin/main >/dev/null; then
                if ! git -C "$INSTALL_DIR" merge-base --is-ancestor HEAD origin/main; then
                    say "  ⚠ checkout at $INSTALL_DIR is BEHIND origin/main — building old code"
                    say "    fix: git -C $INSTALL_DIR fetch origin && git -C $INSTALL_DIR checkout -B main origin/main"
                fi
            fi
            return
        fi
    fi
    if [[ -d "$INSTALL_DIR" ]]; then
        say "→ Moving old checkout aside: $INSTALL_DIR -> $INSTALL_DIR.pre-fork-$STAMP"
        mv "$INSTALL_DIR" "$INSTALL_DIR.pre-fork-$STAMP"
    fi
    say "→ Cloning fork into $INSTALL_DIR..."
    if ! git clone --quiet --branch main "$FORK_SRC" "$INSTALL_DIR" 2>"$SCRATCH_DIR/hermes-takeover-clone.log"; then
        if ! git clone --quiet --branch main "$FORK_HTTPS" "$INSTALL_DIR" 2>>"$SCRATCH_DIR/hermes-takeover-clone.log"; then
            echo "✗ clone failed (tried FORK_SRC then HTTPS) — root cause:" >&2
            tail -5 "$SCRATCH_DIR/hermes-takeover-clone.log" >&2
            die "if rate-limited: clone manually and rerun with FORK_SRC=/path/to/clone"
        fi
    fi
    # Pin origin to the fork repo regardless of where the tree came from.
    git -C "$INSTALL_DIR" remote set-url origin "$FORK_HTTPS" 2>/dev/null || true
}

# ── 4. Environment ──────────────────────────────────────────────────────────
build_venv() {
    say "→ Preparing PM-managed Python and dependencies..."
    bash "$INSTALL_DIR/scripts/install.sh" --dir "$INSTALL_DIR" \
        --hermes-home "$HERMES_HOME" --non-interactive --stage python-deps
}

# ── 5. Launcher ─────────────────────────────────────────────────────────────
install_launcher() {
    # Use the same publication and product builds as a fresh source install.
    # A .venv launcher bypasses PM's selected generation after the next update.
    local stage
    for stage in products complete; do
        bash "$INSTALL_DIR/scripts/install.sh" --dir "$INSTALL_DIR" \
            --hermes-home "$HERMES_HOME" --non-interactive --stage "$stage"
    done
    [[ -x "$LAUNCHER" ]] || die "PM did not publish $LAUNCHER"
}

# ── 6. Migrate state ────────────────────────────────────────────────────────
migrate_state() {
    if [[ -f "$HERMES_HOME/.hermes-vault" ]]; then
        say "○ Vault already present at $HERMES_HOME — skipping migration"
        return 0
    fi
    say "→ Migrating $HERMES_HOME onto the encrypted vault..."
    say "  (full tar backup first)"
    # Fork policy: the master passphrase is NEVER created from or read by a
    # non-interactive context (curl|bash has no TTY trust boundary for a
    # passphrase; env vars leak via /proc/environ, children, EnvironmentFiles).
    # Non-interactive installs create a KEY-ONLY vault: the private key file
    # (0600) is the sole credential. A human can later add a passphrase slot
    # interactively ('hermes secure-vault migrate' offers it on a TTY).
    if _tty_available; then
        "$LAUNCHER" secure-vault migrate --yes
    else
        KEY_OUT="${HERMES_VAULT_KEY_OUT:-$HERMES_HOME/vault.key}"
        if ! "$LAUNCHER" secure-vault migrate --yes --key-only --key-out "$KEY_OUT"; then
            die "key-only migration failed — run 'hermes secure-vault migrate' interactively"
        fi
        say ""
        say "✓ Vault created. Private key (ONLY credential): $KEY_OUT"
        say "  Daemons: HERMES_VAULT_PRIVATE_KEY=$KEY_OUT (a PATH, never a passphrase)"
        say "  Interactive passphrase slot: hermes secure-vault add-key later, from a terminal"
    fi
}

_tty_available() {
    # stdin is the script under curl | bash; /dev/tty is the human's terminal.
    (: </dev/tty) 2>/dev/null
}

main() {
    require_quiescent_home
    swap_source
    build_venv
    install_launcher
    # Secrets-management bootstrap (agentic workflow): the 1Password CLI is
    # part of this project's install surface. Best-effort — a failed install
    # never blocks the takeover; rerun scripts/install_op.sh manually.
    if bash "$INSTALL_DIR/scripts/install_op.sh" --check >/dev/null 2>&1; then
        say "→ 1Password CLI (op): already installed"
    else
        say "→ Installing 1Password CLI (op)…"
        bash "$INSTALL_DIR/scripts/install_op.sh" || say "  ○ op install failed — run scripts/install_op.sh later"
    fi
    require_quiescent_home
    migrate_state
    say ""
    say "✓ Fork installed at $INSTALL_DIR"
    say "  Launcher : $LAUNCHER"
    say "  Home     : $HERMES_HOME (encrypted)"
    if [[ -d "$INSTALL_DIR.pre-fork-$STAMP" ]]; then
        say "  Old tree : $INSTALL_DIR.pre-fork-$STAMP (delete when satisfied)"
    fi
    say ""
    say "Next: hermes secure-vault status   # verify the vault"
    say "      hermes chat                  # normal use (prompts for master password)"
}

# Keep the real preparation functions available to the installation harness.
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
    main "$@"
fi
