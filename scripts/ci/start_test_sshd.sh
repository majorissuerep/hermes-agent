#!/usr/bin/env bash
# Start a throwaway sshd container for the real SSH terminal-backend tests.
#
#   scripts/ci/start_test_sshd.sh <state-dir> [port]
#
# Generates (once per state dir) a client keypair and a host key, starts a
# Debian sshd on 127.0.0.1:<port> (default 2222) that accepts only that key for
# user `hermes`, waits until it answers, and prints the HERMES_TEST_SSH_*
# variables tests/tools/test_ssh_environment.py reads (appended to $GITHUB_ENV
# too when it is set). `docker rm -f hermes-test-sshd` stops it.
#
# The image is a plain Debian (13, current stable) with GNU tar and bash, the shape of a real remote
# host (the backend's file sync uses `tar --no-overwrite-dir`). Container-side setup lives in
# test_sshd_container_setup.sh, mounted read-only at /setup.
set -euo pipefail

state_dir="${1:?usage: start_test_sshd.sh <state-dir> [port]}"
port="${2:-2222}"
image="${HERMES_TEST_SSHD_IMAGE:-debian:13-slim}"
name="hermes-test-sshd"

mkdir -p "$state_dir"
state_dir="$(cd "$state_dir" && pwd)"
[ -f "$state_dir/id_ed25519" ] || ssh-keygen -q -t ed25519 -N '' -C hermes-test -f "$state_dir/id_ed25519"
[ -f "$state_dir/host_ed25519" ] || ssh-keygen -q -t ed25519 -N '' -C hermes-test-host -f "$state_dir/host_ed25519"
cp "$state_dir/id_ed25519.pub" "$state_dir/authorized_keys"
read -r host_key_type host_key_value _ < "$state_dir/host_ed25519.pub"
known_host="[127.0.0.1]:$port"
if [ "$port" = 22 ]; then known_host=127.0.0.1; fi
printf '%s %s %s\n' "$known_host" "$host_key_type" "$host_key_value" > "$state_dir/known_hosts"
# OpenSSH resolves its user files through passwd, independently of $HOME.
# Configure the real ssh/scp clients for this fixture through private PATH launchers.
mkdir -p "$state_dir/bin"
for client in ssh scp; do
  if [ -f "$state_dir/bin/$client" ]; then continue; fi
  native_client="$(command -v "$client")"
  printf '#!/usr/bin/env bash\nexec %q -F /dev/null -o GlobalKnownHostsFile=/dev/null -o StrictHostKeyChecking=yes -o UserKnownHostsFile=%q "$@"\n' \
    "$native_client" "$state_dir/known_hosts" > "$state_dir/bin/$client"
  chmod 700 "$state_dir/bin/$client"
done
export PATH="$state_dir/bin:$PATH"

existing_id="$(docker ps -aq --filter "name=^/${name}$")"
if [ -n "$existing_id" ]; then
  existing_owner="$(docker inspect -f '{{ index .Config.Labels "dev.hermes.sshd-fixture" }}' "$existing_id")"
  if [ "$existing_owner" != "$state_dir" ]; then
    echo "sshd fixture name is owned by another run; choose a separate Docker context" >&2
    exit 1
  fi
  docker rm -f "$existing_id" >/dev/null
fi
setup="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/test_sshd_container_setup.sh"
docker run -d --name "$name" --label "dev.hermes.sshd-fixture=$state_dir" -p "127.0.0.1:${port}:22" \
  -v "$state_dir:/keys:ro" -v "$setup:/setup/setup.sh:ro" \
  "$image" bash /setup/setup.sh >/dev/null

for _ in $(seq 1 120); do
  if ssh -i "$state_dir/id_ed25519" -p "$port" -o BatchMode=yes -o ConnectTimeout=2 \
      -o StrictHostKeyChecking=accept-new hermes@127.0.0.1 true 2>/dev/null; then
    ready=1
    break
  fi
  if [ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null)" != true ]; then
    docker logs "$name" >&2 || true
    echo "sshd container exited before accepting connections" >&2
    exit 1
  fi
  sleep 1
done
if [ -z "${ready:-}" ]; then
  docker logs "$name" >&2 || true
  echo "sshd did not accept the test key within 120s" >&2
  exit 1
fi

env_lines="HERMES_TEST_SSH_HOST=127.0.0.1
HERMES_TEST_SSH_USER=hermes
HERMES_TEST_SSH_PORT=$port
HERMES_TEST_SSH_KEY=$state_dir/id_ed25519"
if [ -n "${GITHUB_ENV:-}" ]; then
  printf '%s\n' "$env_lines" >> "$GITHUB_ENV"
  printf 'PATH=%s\n' "$PATH" >> "$GITHUB_ENV"
fi
printf '%s\n' "$env_lines"
printf 'PATH=%q:$PATH\n' "$state_dir/bin"
