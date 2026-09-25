#!/usr/bin/env bash
# Fork takeover: replace an existing hermes-agent install with the
# majorissuerep/hermes-agent encrypted fork, preserving and migrating all
# sessions/configs onto the master-password vault.
#
# What it does:
#   1. Stops running hermes processes (gateway/desktop serve) cleanly.
#   2. Moves the old source checkout aside (kept, not deleted).
#   3. Clones the fork into the install location.
#   4. Reuses the existing venv approach: builds a fresh venv from the lock.
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
INSTALL_DIR="$HERMES_HOME/hermes-agent"
LAUNCHER="$HOME/.local/bin/hermes"
STAMP="$(date +%Y%m%d-%H%M%S)"
QUIET=0
[[ "${1:-}" == "-q" || "${1:-}" == "--quiet" ]] && QUIET=1

say() { [[ $QUIET -eq 1 ]] || echo "$@"; }
die() { echo "✗ $*" >&2; exit 1; }

command -v git >/dev/null || die "git not found"

# ── 1. Stop live processes that hold DB handles ────────────────────────────
stop_hermes() {
    say "→ Stopping running Hermes services..."
    systemctl --user stop 'hermes-gateway*' 2>/dev/null || true
    # Kill EVERY hermes process from ANY venv (old install, respawned
    # gateway, stale --yolo session, kernel runners). A surviving
    # plaintext-mode process fights the vault mid-migration and clobbers
    # sealed envelopes (real incident: watchdog-respawned old-venv gateway
    # NUL-stripped a sealed .env into garbage).
    pkill -f 'hermes-agent/. *gateway run' 2>/dev/null || true
    pkill -f 'gateway run' 2>/dev/null || true
    pkill -f 'hermes_kernel_runner' 2>/dev/null || true
    pkill -f 'hermes-agent.*--yolo' 2>/dev/null || true
    pkill -f 'hermes-agent/hermes ' 2>/dev/null || true
    sleep 1
    # Watchdogs respawn their victims: after the first sweep, kill any
    # survivor once more and report if something STILL refuses to die.
    if pgrep -f 'hermes-agent/(venv|.venv)/bin' >/dev/null 2>&1; then
        pkill -9 -f 'hermes-agent/(venv|.venv)/bin' 2>/dev/null || true
        sleep 1
        pgrep -f 'hermes-agent/(venv|.venv)/bin' >/dev/null 2>&1 \
            && say "  ⚠ hermes processes still alive — stop them before migrating (pgrep -af hermes)"
    fi
    # desktop serve backend dies with the app; give DBs a moment to close
    sleep 2
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
    if ! git clone --quiet --branch main "$FORK_SRC" "$INSTALL_DIR" 2>/tmp/hermes-takeover-clone.log; then
        if ! git clone --quiet --branch main "$FORK_HTTPS" "$INSTALL_DIR" 2>>/tmp/hermes-takeover-clone.log; then
            echo "✗ clone failed (tried FORK_SRC then HTTPS) — root cause:" >&2
            tail -5 /tmp/hermes-takeover-clone.log >&2
            die "if rate-limited: clone manually and rerun with FORK_SRC=/path/to/clone"
        fi
    fi
    # Pin origin to the fork repo regardless of where the tree came from.
    git -C "$INSTALL_DIR" remote set-url origin "$FORK_HTTPS" 2>/dev/null || true
}

# ── 4. Environment ──────────────────────────────────────────────────────────
build_venv() {
    say "→ Building venv from the lock..."
    cd "$INSTALL_DIR"
    # Python >=3.14 is rejected by requires-python; prefer explicit 3.13/3.11
    # interpreters before a bare python3 that may be too new.
    pick_python() {
        for cand in python3.13 python3.12 python3.11 python3; do
            command -v "$cand" >/dev/null || continue
            if "$cand" -c "import sys; sys.exit(0 if sys.version_info < (3, 14) and sys.version_info >= (3, 11) else 1)" 2>/dev/null; then
                echo "$cand"; return 0
            fi
        done
        return 1
    }
    if ! command -v uv >/dev/null; then
        # macOS/stock hosts have no uv and often no python 3.11-3.13 (CLT ships
        # 3.9); the pip fallback below would die at pick_python. uv provisions a
        # managed 3.11 itself, so installing it is the self-sufficient path.
        say "→ uv not found — installing to ~/.local/bin (astral.sh install script)..."
        if curl -fsSL https://astral.sh/uv/install.sh | sh >/tmp/hermes-takeover-uv.log 2>&1; then
            export PATH="$HOME/.local/bin:$PATH"
        else
            say "  ○ uv install failed — falling back to system python (needs 3.11-3.13):"
            tail -3 /tmp/hermes-takeover-uv.log >&2 || true
        fi
    fi
    if command -v uv >/dev/null; then
        # uv provisions a managed 3.11 itself — no system interpreter needed
        # (hosts whose only python3 is >=3.14 would otherwise die here).
        uv venv --python 3.11 .venv >/dev/null 2>&1 || uv venv .venv >/dev/null 2>&1 || true
        [ -x .venv/bin/python ] || {
            PYBIN=$(pick_python) && "$PYBIN" -m venv .venv
        }
        # Lock-first: 'uv sync' is deterministic and never re-resolves.
        # 'uv pip install -e .' (lockless) also hard-fails on any invalid
        # PEP 440 version string in pyproject — keep it only as a fallback.
        # NO --all-extras: the matrix extra (python-olm) has manylinux x86_64
        # cp310-cp313 wheels only — on macOS/Windows/py3.14 uv would try an
        # sdist build (libolm+cmake) and the whole takeover dies (MS73-HB1
        # fell back to base deps this way). Messaging platforms land via the
        # [messaging] extra; everything heavy stays lazy-installed per [all].
        if ! uv sync --all-extras --no-extra matrix >/tmp/hermes-takeover-sync.log 2>&1; then
            if ! uv sync --extra messaging >/tmp/hermes-takeover-sync.log 2>&1; then
                if ! uv pip install --python .venv/bin/python -e . >/tmp/hermes-takeover-install.log 2>&1; then
                    echo "✗ dependency install failed — root cause:" >&2
                    tail -5 /tmp/hermes-takeover-sync.log /tmp/hermes-takeover-install.log >&2
                    die "see above; logs kept in /tmp/hermes-takeover-*.log"
                fi
            fi
        fi
    else
        PYBIN=$(pick_python) || die "no python between 3.11 and 3.13 found"
        "$PYBIN" -m venv .venv || die "venv creation failed"
        .venv/bin/pip install --quiet --upgrade pip >/dev/null
        if ! .venv/bin/pip install --quiet -e . >/tmp/hermes-takeover-install.log 2>&1; then
            echo "✗ pip install failed — root cause:" >&2
            tail -8 /tmp/hermes-takeover-install.log >&2
            die "see above"
        fi
    fi
    [[ -x .venv/bin/python ]] || die "venv missing python"
}

# ── 5. Launcher ─────────────────────────────────────────────────────────────
install_launcher() {
    say "→ Pointing 'hermes' at the fork..."
    mkdir -p "$(dirname "$LAUNCHER")"
    cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
exec "$INSTALL_DIR/.venv/bin/python" -m hermes_cli.main "\$@"
EOF
    chmod +x "$LAUNCHER"
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
    # Same probe as hermes_cli/vault_gate._tty_available: a real terminal is reachable
    # through /dev/tty (NOT stdin.isatty — under curl|bash stdin is the script pipe).
    local _py
    _py="python3"
    command -v "$_py" >/dev/null 2>&1 || _py="$INSTALL_DIR/.venv/bin/python"
    "$_py" -c 'import os,sys
try:
    sys.exit(0 if os.isatty(os.open("/dev/tty", os.O_RDWR)) else 1)
except OSError:
    sys.exit(1)'
}

main() {
    stop_hermes
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

main "$@"
