#!/usr/bin/env bash
# Noninteractive external executor shells must keep the owning Hermes command.
# This is installed code; no key, grant or passphrase is written here.
if [ -n "${HERMES_PREVIOUS_BASH_ENV:-}" ] && [ "$HERMES_PREVIOUS_BASH_ENV" != "${BASH_ENV:-}" ]; then
    source "$HERMES_PREVIOUS_BASH_ENV"
fi
if [ -n "${HERMES_MANAGEMENT_BIN:-}" ]; then
    unalias hermes 2>/dev/null || true
    export PATH="$HERMES_MANAGEMENT_BIN${PATH:+:$PATH}"
fi
