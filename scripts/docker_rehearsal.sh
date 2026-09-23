#!/usr/bin/env bash
# Isolated Docker rehearsal of the fork takeover:
#   phase 1: install NATIVE hermes (upstream NousResearch), create real state
#            (sessions with messages, config, memory, logs)
#   phase 2: run the fork's scripts/takeover.sh (installs majorissuerep/hermes-agent)
#   phase 3: verify — sessions/config intact, at-rest bytes encrypted, no
#            plaintext copies, restorable from the pre-migration backup
# Usage: bash docker_rehearsal.sh [image]
set -uo pipefail

IMAGE="${1:-docker.io/library/fedora:43}"
CTF="hermes-takeover-rehearsal"
PASS='Docker-Rehearsal-Pass-9'
WORK=/home/luoman/hermes-rehearsal          # scratch outside the hermes home

cleanup() { docker rm -f "$CTF" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker_run() {  # run inside the container as the rehearsal user (login shell, no stdin)
    docker exec -u luoman -e HOME=/home/luoman "$CTF" bash -lc "$1"
}

phase1_native() {
    echo "════ PHASE 1: native (upstream) hermes install + real state ════"
    docker run -d --name "$CTF" "$IMAGE" sleep infinity >/dev/null
    docker exec "$CTF" bash -c '
        dnf -y install git python3.13 python3-pip sudo >/dev/null 2>&1
        useradd -m luoman 2>/dev/null || true
        echo "luoman ALL=(root) NOPASSWD:ALL" > /etc/sudoers.d/luoman
        git --version && python3.13 --version
    '
    # Native hermes: upstream clone at the documented install location
    docker_run '
        set -e
        git clone --quiet https://github.com/NousResearch/hermes-agent.git ~/.hermes/hermes-agent
        cd ~/.hermes/hermes-agent
        python3.13 -m venv .venv
        .venv/bin/pip install --quiet --upgrade pip >/dev/null
        .venv/bin/pip install --quiet -e . >/dev/null 2>&1 || .venv/bin/pip install --quiet -e ".[dev]" >/dev/null
        mkdir -p ~/.local/bin
        printf "#!/usr/bin/env bash\nexec %s/.hermes/hermes-agent/.venv/bin/python -m hermes_cli.main \"\$@\"\n" "$HOME" > ~/.local/bin/hermes
        chmod +x ~/.local/bin/hermes
        hermes --version
    '
    # Realistic state: 2 sessions with multi-message transcripts + tool calls,
    # meta marker, config keys, log lines
    docker_run '
        set -e
        cd ~/.hermes/hermes-agent
        .venv/bin/python - <<PYEOF
from hermes_state import SessionDB
with SessionDB() as db:
    db.set_meta("rehearsal_marker", "native-before-takeover")
    s1 = "rehearsal-session-alpha"
    db.append_message(s1, "user", "What is the debt total in Enishia?")
    db.append_message(s1, "assistant", "The total debt is 1,000,000 gold, payable to the church.",
                      tool_name="terminal", token_count=42)
    db.append_message(s1, "user", "Which party members can I recruit first?")
    db.append_message(s1, "assistant", "Nami the healer and Bray the merchant are the earliest.")
    s2 = "rehearsal-session-beta"
    db.append_message(s2, "user", "Summarize the corruption stages in Ambrosias wettest dreams.")
    db.append_message(s2, "assistant", "Stage 1 purity through stage 5 ruin, gated by debt events.")
print("sessions written")
PYEOF
        hermes config set model.default "anthropic/claude-sonnet-4-20250514"
        hermes config set memory.enabled true
        hermes config set ui.theme dark
        echo "--- native state:"
        ls ~/.hermes/*.db; hermes config get model.default
    '
}

phase2_takeover() {
    echo "════ PHASE 2: fork takeover via scripts/takeover.sh ════"
    docker_run '
        set -e
        mkdir -p '"$WORK"'
        git clone --quiet --branch vault --depth 1 https://github.com/majorissuerep/hermes-agent.git '"$WORK"'/fork-src
        bash '"$WORK"'/fork-src/scripts/takeover.sh
    '
    # takeover.sh migrate step runs interactively; drive it with the env password
    if ! docker_run 'test -f ~/.hermes/.hermes-vault' 2>/dev/null; then
        echo "(vault not yet created — running migrate with env password)"
        docker_run 'HERMES_MASTER_PASSWORD='"$PASS"' bash '"$WORK"'/fork-src/scripts/takeover.sh' || true
    fi
}

phase3_verify() {
    echo "════ PHASE 3: verification battery ════"
    # [1] Sessions survived and read back under the master password
    docker_run '
        set -e
        export HERMES_MASTER_PASSWORD="'"$PASS"'"
        cd ~/.hermes/hermes-agent
        echo "[1] session read-back under the fork:"
        .venv/bin/python - <<PYEOF
from hermes_state import SessionDB
with SessionDB() as db:
    print("   marker:", db.get_meta("rehearsal_marker"))
    rows = db._read_all("SELECT session_id, role, content FROM messages WHERE session_id LIKE \"rehearsal%\" ORDER BY id")
    for r in rows:
        print("   msg:", r["session_id"], r["role"], (r["content"] or "")[:44])
    assert len(rows) == 6, f"expected 6 rehearsal messages, got {len(rows)}"
    assert db.get_meta("rehearsal_marker") == "native-before-takeover"
print("   SESSION+META MIGRATION OK")
PYEOF
    '
    # [2] Config still reads back through the envelope
    docker_run '
        export HERMES_MASTER_PASSWORD="'"$PASS"'"
        echo "[2] config read-back:"
        hermes config get model.default
        hermes config get ui.theme
    '
    # [3] At-rest bytes: DB and config must NOT be plaintext
    docker_run '
        echo "[3] at-rest encryption scan:"
        if grep -aq "SQLite format 3" ~/.hermes/state.db 2>/dev/null; then echo "   FAIL: state.db plaintext SQLite header"; else echo "   OK: state.db has no plaintext SQLite header"; fi
        if grep -aq "rehearsal-session-alpha" ~/.hermes/state.db; then echo "   FAIL: session id plaintext in state.db"; else echo "   OK: no session ids plaintext in state.db"; fi
        if grep -aq "claude-sonnet" ~/.hermes/config.yaml 2>/dev/null; then "   FAIL: config.yaml plaintext"; else echo "   OK: config.yaml not plaintext"; fi
        for f in ~/.hermes/logs/*.log; do
            [ -e "$f" ] || continue
            if grep -aqiE "plugin|registered" "$f"; then echo "   WARN: plaintext line in $f"; else echo "   OK: $f not plaintext"; fi
        done
    '
    # [4] Whole-home plaintext sweep (excluding code dirs and the backup tar)
    docker_run '
        echo "[4] whole-home sweep for secret-shaped plaintext:"
        grep -rlaE "rehearsal-session|claude-sonnet|debt total|native-before-takeover" \
          --include="*.db" --include="*.yaml" --include="*.json" --include="*.log" \
          --include="*.jsonl" --include=".env" --exclude-dir=hermes-agent \
          ~/.hermes 2>/dev/null | tee '"$WORK"'/leaks.txt
        hits=$(wc -l < '"$WORK"'/leaks.txt)
        echo "   plaintext hits: ${hits:-0} (0 required)"
    '
    # [5] Restorable: pre-migration backup tar exists and its plaintext round-trips
    docker_run '
        set -e
        echo "[5] restore drill from pre-migration backup:"
        TAR=$(ls ~/hermes-premigration-*.tar ~/.hermes-premigration-*.tar 2>/dev/null | head -1)
        [ -n "$TAR" ] || { echo "   FAIL: no premigration tar"; exit 1; }
        echo "   backup: $TAR ($(du -h "$TAR" | cut -f1))"
        mkdir -p '"$WORK"'/restore && cd '"$WORK"'/restore
        tar xf "$TAR"
        # tar stores paths relative to the home root (state.db, config.yaml, ...)
        grep -aq "SQLite format 3" state.db && echo "   OK: restored state.db is plaintext SQLite (usable by any tool)"
        grep -aq "rehearsal-session-alpha" state.db && echo "   OK: restored state.db contains the sessions"
        echo "   RESTORE DRILL OK — deleting plaintext copies now"
        rm -rf '"$WORK"'/restore
        rm -rf ~/.hermes/hermes-agent.pre-fork-* 2>/dev/null || true
        rm -f "$TAR"
    '
    # [6] After cleanup: NO unencrypted copies remain anywhere
    docker_run '
        echo "[6] post-cleanup rescan for plaintext copies:"
        hits=$(grep -rlaE "rehearsal-session-alpha|native-before-takeover" ~ 2>/dev/null | grep -v hermes-rehearsal | wc -l)
        echo "   files containing plaintext session data under /home/luoman: $hits (0 required)"
    '
    # [7] Wrong password must fail closed
    docker_run '
        echo "[7] wrong-password refusal:"
        if HERMES_MASTER_PASSWORD=wrong-password-123 hermes config get model.default; then echo "   FAIL: accepted wrong password"; else echo "   OK: refused (exit $?)"; fi
    '
    # [8] Updater pinned to the fork (no way out)
    docker_run '
        echo "[8] updater origin check:"
        git -C ~/.hermes/hermes-agent remote get-url origin
    '
}

main() {
    cleanup
    phase1_native
    phase2_takeover
    phase3_verify
    echo "════ rehearsal complete ════"
}

main "$@"
