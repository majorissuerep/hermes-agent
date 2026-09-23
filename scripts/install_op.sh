#!/usr/bin/env bash
# Bootstrap the 1Password CLI (`op`) — part of this fork's install surface.
#
# The fork's secrets model: .env holds only `op://` SECRET REFERENCES
# (encrypted at rest by the vault); values resolve in memory at load via
# `op`. The `op` binary itself is installed/managed here.
#
# Idempotent: an existing op on PATH wins; --check exits 0/2 only.
set -uo pipefail

CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

have_op() { command -v op >/dev/null 2>&1; }

if have_op; then
    if [ "$CHECK_ONLY" -eq 1 ]; then
        echo "op $(op --version) already installed at $(command -v op)"
        exit 0
    fi
    echo "✓ op $(op --version) already installed at $(command -v op)"
    exit 0
fi
if [ "$CHECK_ONLY" -eq 1 ]; then
    echo "op NOT installed"
    exit 2
fi

echo "→ Installing 1Password CLI (op)…"

install_linux_rpm() {
    sudo rpm --import https://downloads.1password.com/linux/keys/1password.asc
    echo -e "[1password]\nname=1Password Stable Channel\nbaseurl=https://downloads.1password.com/linux/rpm/stable/\$basearch\nenabled=1\ngpgcheck=1\nrepo_gpgcheck=1\ngpgkey=https://downloads.1password.com/linux/keys/1password.asc" | sudo tee /etc/yum.repos.d/1password.repo >/dev/null
    sudo dnf install -y 1password-cli
}
install_linux_deb() {
    curl -fsSL https://downloads.1password.com/linux/keys/1password.asc | sudo gpg --dearmor --output /usr/share/keyrings/1password-archive-keyring.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/1password-archive-keyring.gpg] https://downloads.1password.com/linux/debian/amd64 stable main" | sudo tee /etc/apt/sources.list.d/1password.list
    sudo mkdir -p /etc/apt/keyrings && sudo cp /usr/share/keyrings/1password-archive-keyring.gpg /etc/apt/keyrings/1password-archive-keyring.gpg 2>/dev/null || true
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
