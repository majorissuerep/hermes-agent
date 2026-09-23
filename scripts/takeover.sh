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
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
INSTALL_DIR="$HERMES_HOME/hermes-agent"
LAUNCHER="$HOME/.local/bin/hermes"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_DIR="$HERMES_HOME/logs"
QUIET=0
[[ "${1:-}" == "-q" || "${1:-}" == "--quiet" ]] && QUIET=1

say() { [[ $QUIET -eq 1 ]] || echo "$@"; }
die() { echo "✗ $*" >&2; exit 1; }

command -v git >/dev/null || die "git not found"

# ── 1. Stop live processes that hold DB handles ────────────────────────────
stop_hermes() {
    say "→ Stopping running Hermes services..."
    systemctl --user stop 'hermes-gateway*' 2>/dev/null || true
    pkill -f 'gateway run' 2>/dev/null || true
    # desktop serve backend dies with the app; give DBs a moment to close
    sleep 2
}

# ── 2/3. Swap the source tree ───────────────────────────────────────────────
swap_source() {
    if [[ -d "$INSTALL_DIR/.git" ]]; then
        origin_url="$(git -C "$INSTALL_DIR" remote get-url origin 2>/dev/null || true)"
        if [[ "$origin_url" == *majorissuerep/hermes-agent* ]]; then
            say "→ Fork already checked out at $INSTALL_DIR; updating..."
            git -C "$INSTALL_DIR" fetch origin --quiet
            git -C "$INSTALL_DIR" reset --hard origin/$(git -C "$INSTALL_DIR" rev-parse --abbrev-ref HEAD) --quiet
            return
        fi
    fi
    if [[ -d "$INSTALL_DIR" ]]; then
        say "→ Moving old checkout aside: $INSTALL_DIR -> $INSTALL_DIR.pre-fork-$STAMP"
        mv "$INSTALL_DIR" "$INSTALL_DIR.pre-fork-$STAMP"
    fi
    say "→ Cloning fork into $INSTALL_DIR..."
    git clone --quiet --branch vault "$FORK_SSH" "$INSTALL_DIR" 2>/dev/null \
        || git clone --quiet --branch vault "$FORK_HTTPS" "$INSTALL_DIR" \
        || die "clone failed (tried SSH then HTTPS)"
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
    PYBIN=$(pick_python) || die "no python between 3.11 and 3.13 found"
    if command -v uv >/dev/null; then
        uv venv --python "$PYBIN" .venv >/dev/null 2>&1 || "$PYBIN" -m venv .venv
        uv pip install --python .venv/bin/python -e . >/dev/null 2>&1 \
            || uv sync --all-extras >/dev/null 2>&1 \
            || die "dependency install failed"
    else
        "$PYBIN" -m venv .venv || die "venv creation failed"
        .venv/bin/pip install --quiet --upgrade pip >/dev/null
        .venv/bin/pip install --quiet -e . >/dev/null 2>&1 || .venv/bin/pip install --quiet sqlcipher3-binary cryptography >/dev/null
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
    say "  (full tar backup first; you will set the master password)"
    "$LAUNCHER" secure-vault migrate --yes
}

main() {
    stop_hermes
    swap_source
    build_venv
    install_launcher
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
