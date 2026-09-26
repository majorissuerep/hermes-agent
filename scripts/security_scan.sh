#!/usr/bin/env bash
# Mandatory security suite: missing tools and engine errors are NOT clean scans.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
umask 077
work=$(mktemp -d "${TMPDIR:-/tmp}/hermes-security.XXXXXXXX") || exit 1
trap 'rm -rf "$work"' EXIT
FAIL=0

run_check() {
    local name="$1" rc
    shift
    printf '\n══ %s ══\n' "$name"
    if ! command -v "$1" >/dev/null 2>&1; then
        printf 'NOT_RUN: %s (required executable missing: %s)\n' "$name" "$1"
        FAIL=1
        return
    fi
    if "$@" >"$work/output" 2>&1; then
        printf 'PASS: %s\n' "$name"
    else
        rc=$?
        # Some engines share exit codes for findings and invocation failures.
        # Never infer success from localized human-readable output.
        printf 'FAIL: %s (findings or engine error; exit %s)\n' "$name" "$rc"
        cat "$work/output"
        FAIL=1
    fi
}

scripts=()
if find . -name '*.sh' -not -path './.git/*' -not -path './.venv/*' \
    -not -path '*/node_modules/*' -print0 >"$work/shell-files"; then
    while IFS= read -r -d '' path; do scripts+=("$path"); done <"$work/shell-files"
    if [ "${#scripts[@]}" -gt 0 ]; then
        run_check shellcheck shellcheck -S warning "${scripts[@]}"
    else
        printf 'NOT_RUN: shellcheck (no shell files selected)\n'; FAIL=1
    fi
else
    printf 'ERROR: shell file discovery failed\n'; FAIL=1
fi
run_check gitleaks gitleaks detect --source . --no-banner --no-git --redact
run_check bandit bandit -r hermes_security/ hermes_cli/vault_cmd.py hermes_cli/vault_gate.py \
    hermes_cli/config_backups.py -q
run_check pip-audit pip-audit
# Semgrep otherwise exits zero even with findings; strict rejects engine warnings.
run_check semgrep semgrep scan --config p/python --config p/security-audit \
    --metrics=off --error --strict --quiet hermes_security/ hermes_cli/vault_cmd.py hermes_cli/vault_gate.py
run_check hadolint hadolint Dockerfile
# All workspaces share the root lockfile; --prefix targets have no lock to audit.
if [ -f package-lock.json ]; then
    run_check 'npm audit (root and workspaces)' npm audit --omit=dev --workspaces --include-workspace-root
else
    printf 'NOT_RUN: npm audit (root lockfile absent)\n'; FAIL=1
fi
# Trivy defaults to exit 0 for findings unless --exit-code is explicit.
run_check trivy trivy fs --skip-dirs .venv,.git,node_modules --scanners secret --exit-code 1 .
if [ "$FAIL" -ne 0 ]; then
    printf '\n══ SECURITY SCAN: FAILED OR INCOMPLETE ══\n'
    exit 1
fi
printf '\n══ SECURITY SCAN: ALL CLEAN ══\n'
