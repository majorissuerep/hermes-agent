#!/usr/bin/env bash
# Real terminal and headless-browser contract, with bounded disposable ownership.
set -euo pipefail
IMAGE="${1:?usage: sandbox-terminal-smoke.sh <image>}"
NAME="terminal-smoke-$$"
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker run -d --name "$NAME" --memory=2g --pids-limit=256 "$IMAGE" >/dev/null
docker exec -u pn "$NAME" bash -euo pipefail -c '
  for binary in python3 node agent-browser chromium jq rg fd tmux rsync; do
    command -v "$binary" >/dev/null
  done
  python3 -c "print(6 * 7)" | grep -qx 42
  test "$(id -u)" = 1000
  test -z "${DISPLAY:-}"
  ! command -v Xvnc
  ! command -v xfdesktop
  ! command -v cua-driver
  chromium --headless --no-sandbox --disable-gpu --disable-dev-shm-usage --dump-dom \
    "data:text/html,<title>TERMINAL_BROWSER_ORACLE</title>" | grep -q TERMINAL_BROWSER_ORACLE
  agent-browser --version
'
