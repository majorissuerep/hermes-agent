#!/usr/bin/env bash
# Noninteractive external executor shells must keep the owning Hermes command.
# This is installed code; no key, grant or passphrase is written here.
if [ -n "${HERMES_PREVIOUS_BASH_ENV:-}" ] && [ "$HERMES_PREVIOUS_BASH_ENV" != "${BASH_ENV:-}" ] \
    && [ -r "$HERMES_PREVIOUS_BASH_ENV" ]; then
    # The caller's existing Bash init is deliberately dynamic.
    # shellcheck source=/dev/null
    source "$HERMES_PREVIOUS_BASH_ENV"
fi
if [ -n "${HERMES_MANAGEMENT_BIN:-}" ]; then
    unalias hermes 2>/dev/null || true
    unset -f hermes 2>/dev/null || true
    # Keep the caller's PATH order; nested shells must not stack duplicate entries.
    hermes_managed_path=":${PATH:-}:"
    while [ "${hermes_managed_path#*:"$HERMES_MANAGEMENT_BIN":}" != "$hermes_managed_path" ]; do
        hermes_managed_path="${hermes_managed_path%%:"$HERMES_MANAGEMENT_BIN":*}:${hermes_managed_path#*:"$HERMES_MANAGEMENT_BIN":}"
    done
    hermes_managed_path="${hermes_managed_path#:}"
    hermes_managed_path="${hermes_managed_path%:}"
    export PATH="$HERMES_MANAGEMENT_BIN${hermes_managed_path:+:$hermes_managed_path}"
    unset hermes_managed_path
fi
