#!/usr/bin/env bash
# OS prerequisites only. No Hermes runtime, tool cache, user state or credentials.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
snapshot=20261001T000000Z
printf '%s  /inputs/bootstrap-ca.deb\n' "$1" | sha256sum --check --strict
dpkg-deb -x /inputs/bootstrap-ca.deb /inputs/bootstrap-ca
mkdir -p /etc/ssl/certs
cat /inputs/bootstrap-ca/usr/share/ca-certificates/mozilla/*.crt > /etc/ssl/certs/ca-certificates.crt
rm -f /etc/apt/sources.list.d/ubuntu.sources
cat > /etc/apt/sources.list <<EOF
deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/$snapshot noble main universe
deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/$snapshot noble-updates main universe
deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/$snapshot noble-security main universe
EOF
apt-get -o Acquire::Retries=3 -o APT::Update::Error-Mode=any update
apt-get -o Acquire::Retries=3 install -y --no-install-recommends \
    ca-certificates curl git python3 libatomic1 procps xz-utils zstd
rm -rf /var/lib/apt/lists/*
useradd --create-home --uid 10001 rehearsal
mkdir -p /rehearsal/work /rehearsal/evidence
git --git-dir=/inputs/source.git fsck --full
git clone /inputs/source.git /source
candidate=$(python3 -c 'import json; print(json.load(open("/inputs/source.json"))["candidate"])')
git -C /source checkout --detach "$candidate"
chown -R rehearsal:rehearsal /source /inputs/source.git /rehearsal
dpkg-query -W -f='${Package}\t${Version}\n' > /rehearsal/os-packages.log
