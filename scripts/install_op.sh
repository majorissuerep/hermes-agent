#!/usr/bin/env bash
# Bootstrap the 1Password CLI (`op`) — part of this fork's install surface.
#
# The fork's secrets model: .env holds only `op://` SECRET REFERENCES
# (encrypted at rest by the vault); values resolve in memory at load via
# `op`. The `op` binary itself is installed/managed here.
#
# Idempotent and curl|bash-safe: NO step ever reads stdin (a prompt under
# `curl | bash` would consume the script stream); an existing op anywhere
# wins; --check exits 0/2 only.
set -uo pipefail

CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

# PATH lookup first, then the distro-packaged absolute locations a
# non-login `curl | bash` environment can miss.
have_op() {
    command -v op >/dev/null 2>&1 && return 0
    local cand
    for cand in /usr/bin/op /usr/local/bin/op /usr/sbin/op /opt/homebrew/bin/op; do
        [[ -x "$cand" ]] && return 0
    done
    return 1
}

if have_op; then
    echo "✓ op $(op --version 2>/dev/null || echo installed) already on this machine"
    exit 0
fi
if [ "$CHECK_ONLY" -eq 1 ]; then
    echo "op NOT installed"
    exit 2
fi

echo "→ Installing 1Password CLI (op)…"

install_linux_rpm() {
    sudo rpm --import https://downloads.1password.com/linux/keys/1password.asc
    printf '%s\n' \
        "[1password]" \
        "name=1Password Stable Channel" \
        "baseurl=https://downloads.1password.com/linux/rpm/stable/\$basearch" \
        "enabled=1" "gpgcheck=1" "repo_gpgcheck=1" \
        "gpgkey=https://downloads.1password.com/linux/keys/1password.asc" \
        | sudo tee /etc/yum.repos.d/1password.repo >/dev/null
    sudo dnf install -y 1password-cli
}
install_linux_deb() {
    # --batch --yes: NEVER prompt (an overwrite confirm under `curl | bash`
    # reads the script stream itself). Writing to a temp file + install -m
    # makes the keyring update atomic and idempotent.
    local tmp_keyring
    tmp_keyring="$(mktemp)"
    if ! curl -fsSL https://downloads.1password.com/linux/keys/1password.asc \
        | gpg --batch --yes --dearmor -o "$tmp_keyring"; then
        rm -f "$tmp_keyring"
        echo "✗ could not fetch/verify the 1Password repo key"
        exit 1
    fi
    sudo install -m 0644 "$tmp_keyring" /usr/share/keyrings/1password-archive-keyring.gpg
    rm -f "$tmp_keyring"
    sudo mkdir -p /etc/apt/keyrings
    sudo cp -f /usr/share/keyrings/1password-archive-keyring.gpg /etc/apt/keyrings/1password-archive-keyring.gpg
    printf '%s\n' \
        "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/1password-archive-keyring.gpg] https://downloads.1password.com/linux/debian/amd64 stable main" \
        | sudo tee /etc/apt/sources.list.d/1password.list >/dev/null
    sudo apt-get update -qq && sudo apt-get install -y 1password-cli
}
install_macos() { brew install 1password-cli; }

OS="$(uname -s)"
case "$OS" in
    Linux)
        if command -v dnf >/dev/null; then install_linux_rpm
        elif command -v apt-get >/dev/null; then install_linux_deb
        else echo "✗ no supported package manager (dnf/apt)"; exit 1; fi
        ;;
    Darwin) install_macos ;;
    *) echo "✗ unsupported OS: $OS"; exit 1 ;;
esac

if have_op; then
    echo "✓ op $(op --version) installed"
    echo "  Next: hermes secrets onepassword setup   # wire references into .env"
    exit 0
fi
echo "✗ op install failed"
exit 1
