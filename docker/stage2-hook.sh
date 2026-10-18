#!/bin/sh
# s6-overlay stage2 hook — runs as root after the supervision tree is
# up but before user services start. Handles UID/GID remap, volume
# chown, config seeding, and skills sync.
#
# Per-service privilege drop happens inside each service's `run` script
# (and in main-wrapper.sh) via s6-setuidgid, not here.
#
# Wired into the image as /etc/cont-init.d/01-hermes-setup by the
# Dockerfile. The shim at docker/entrypoint.sh forwards to this script
# so external references to docker/entrypoint.sh still work.
#
# NB: cont-init.d scripts run with no arguments — the user's CMD args
# are NOT visible here. That's fine: we use Architecture B (s6-overlay
# main-program model), so main-wrapper.sh runs the CMD with full
# stdin/stdout/stderr access and handles arg parsing there.

set -eu

HERMES_HOME="${HERMES_HOME:-/opt/data}"
INSTALL_DIR="/opt/hermes"

# Drop to hermes via s6-setuidgid, but skip it when already non-root.
as_hermes() { [ "$(id -u)" = 0 ] || { "$@"; return; }; s6-setuidgid hermes "$@"; }

# --- Reject the unsupported `docker run --user <uid>:<gid>` start ---
# Detect the case where the container was launched with `--user` pinned to an
# arbitrary host UID (the classic `--user $(id -u):$(id -g)` invocation people
# used in the tini era to make container-written files match their host user).
#
# Under s6-overlay this no longer works: the bootstrap (UID remap, data-volume
# ownership, config seeding) requires root, and it is skipped when the container
# starts non-root. The baked install tree under /opt/hermes is intentionally
# root-owned and non-writable; mutable runtime state must live under
# $HERMES_HOME. An arbitrary `--user` UID therefore cannot repair or populate
# the data volume, and startup fails with EACCES. See #34837 for the
# supervision-tree side of this.
#
# The supported way to match host-side ownership is to start as root (the image
# default) and pass HERMES_UID/HERMES_GID — or the PUID/PGID aliases — which the
# remap block below consumes via usermod/groupmod + targeted chown. That gives
# the exact same outcome (files owned by your host UID) without breaking s6.
#
# preinit runs setuid-root (euid=0) but cont-init.d hooks run with the real UID
# the container was started as, so `id -u` here is the host UID (e.g. 1000), and
# `id -u hermes` is the unremapped build UID (10000) because no root-only remap
# could run. root starts (id -u = 0) and the normal supervised drop to the
# hermes UID are both unaffected.
cur_uid="$(id -u)"
if [ "$cur_uid" != 0 ] && [ "$cur_uid" != "$(id -u hermes)" ]; then
    cat >&2 <<EOF
[stage2] ERROR: container started with --user $cur_uid (an arbitrary, non-hermes UID).

This is not supported under the s6-overlay image. The container bootstrap
(UID remap, data-volume ownership, config seeding) needs to start as root,
and the baked /opt/hermes install tree is intentionally root-owned and
non-writable, so a pinned --user UID cannot repair startup state — startup
will fail.

To make container-written files match your HOST user, DON'T use --user.
Start the container as root (the default) and pass your host UID/GID instead:

    docker run -e HERMES_UID=\$(id -u) -e HERMES_GID=\$(id -g) ...

NAS users (Synology / unRAID / UGOS) can use the PUID/PGID aliases:

    docker run -e PUID=\$(id -u) -e PGID=\$(id -g) ...

The image remaps the hermes user to that UID/GID at boot and chowns the data
volume accordingly, so files land owned by your host user — the same outcome
--user was being used for, without breaking the supervision tree.
EOF
    exit 1
fi

# --- Bootstrap HERMES_HOME as root ---
# Create the directory (and any missing parents) while we still have root
# privileges so the chown checks below see real metadata and the later
# `s6-setuidgid hermes mkdir -p` block doesn't EACCES on root-owned
# ancestors. Without this, custom HERMES_HOME paths whose parents only
# root can create (e.g. `HERMES_HOME=/home/hermes/.hermes` in a Compose
# file, or any path under a fresh / not pre-populated by the image)
# fail on first boot with `mkdir: cannot create directory '/...': Permission
# denied` and the cont-init hook exits non-zero. Idempotent — `mkdir -p`
# is a no-op if the dir already exists. (#18482, salvages #18488)
mkdir -p "$HERMES_HOME"

# Numeric UID/GID validation: must be digits only, non-root, 1-65534.
# NAS hosts such as Unraid commonly use low non-root IDs (99:100).
validate_uid_gid() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
        *) [ "$1" -ge 1 ] && [ "$1" -le 65534 ] ;;
    esac
}

# --- UID/GID remap ---
# Accept PUID/PGID as aliases for HERMES_UID/HERMES_GID.  NAS users (UGOS,
# Synology, unRAID) expect the LinuxServer.io PUID/PGID convention and
# bind-mount /opt/data from a host directory owned by their own UID; without
# this alias those vars are silently ignored and the s6-setuidgid drop to
# UID 10000 leaves the runtime unable to read the volume.  HERMES_UID/
# HERMES_GID still win when both are set.  See #15290, salvages #25872.
HERMES_UID="${HERMES_UID:-${PUID:-}}"
HERMES_GID="${HERMES_GID:-${PGID:-}}"

if [ -n "${HERMES_UID:-}" ] && validate_uid_gid "$HERMES_UID" && [ "$HERMES_UID" != "$(id -u hermes)" ]; then
    echo "[stage2] Changing hermes UID to $HERMES_UID"
    usermod -u "$HERMES_UID" hermes
fi
if [ -n "${HERMES_GID:-}" ] && validate_uid_gid "$HERMES_GID" && [ "$HERMES_GID" != "$(id -g hermes)" ]; then
    echo "[stage2] Changing hermes GID to $HERMES_GID"
    # -o allows non-unique GID (e.g. macOS GID 20 "staff" may already
    # exist as "dialout" in the Debian-based container image).
    groupmod -o -g "$HERMES_GID" hermes 2>/dev/null || true
fi

# --- Docker socket group membership (docker-in-docker / DooD) ---
# When the user bind-mounts the host Docker daemon socket
# (`-v /var/run/docker.sock:/var/run/docker.sock`) to use the `docker`
# terminal backend from inside the container, the socket is owned by the
# host's `docker` group (or root). The supervised hermes user (UID 10000)
# is not a member of any group that matches the socket's GID, so every
# `docker` invocation EACCES'es and `check_terminal_requirements()` fails.
# See #16703.
#
# Granting the supp group via `docker run --group-add <gid>` alone is
# NOT sufficient with our s6-setuidgid privilege drop: s6-setuidgid (and
# gosu, the older shim) calls initgroups() for the target user, which
# rebuilds the supplementary group list from /etc/group. Without an
# /etc/group entry whose GID matches the socket, the kernel-granted
# supp group is silently wiped between PID 1 and the dropped process.
# Confirmed empirically: `--group-add 998` alone leaves the dropped
# hermes process with `Groups: 10000` (998 gone); after this hook adds
# the entry, the dropped process has `Groups: 998 10000` as expected.
#
# Fix: detect the socket's GID at boot and ensure /etc/group has a
# matching entry that includes hermes. Idempotent across container
# restarts. Skipped silently when no socket is bind-mounted.
#
# Handles the awkward corner cases:
#   - socket owned by GID 0 (root) — some Podman setups; usermod -aG root
#   - socket GID already used by a known container group (e.g. tty=5):
#     reuse that group's name rather than creating a duplicate
#   - hermes is already a member of the right group (idempotent restart)
#   - chown/groupadd failures under rootless containers — non-fatal
for sock in /var/run/docker.sock /run/docker.sock; do
    [ -S "$sock" ] || continue
    sock_gid=$(stat -c '%g' "$sock" 2>/dev/null) || continue
    [ -n "$sock_gid" ] || continue
    # Already a member? Nothing to do.
    if id -G hermes 2>/dev/null | tr ' ' '\n' | grep -qx "$sock_gid"; then
        echo "[stage2] hermes already in group $sock_gid for $sock"
        break
    fi
    # Resolve or create a group name for this GID.
    sock_group=$(getent group "$sock_gid" 2>/dev/null | cut -d: -f1)
    if [ -z "$sock_group" ]; then
        sock_group="hostdocker"
        if ! groupadd -g "$sock_gid" "$sock_group" 2>/dev/null; then
            echo "[stage2] Warning: groupadd -g $sock_gid $sock_group failed; skipping docker socket group setup"
            break
        fi
        echo "[stage2] Created group $sock_group (GID $sock_gid) for Docker socket"
    fi
    if usermod -aG "$sock_group" hermes 2>/dev/null; then
        echo "[stage2] Added hermes to group $sock_group (GID $sock_gid) for $sock"
    else
        echo "[stage2] Warning: usermod -aG $sock_group hermes failed; docker backend may fail with EACCES"
    fi
    break
done

# --- Fix ownership of data volume ---
# When HERMES_UID is remapped or the top-level $HERMES_HOME isn't owned by
# the runtime hermes UID, restore ownership to hermes — but ONLY for the
# directories hermes actually writes to. The full $HERMES_HOME may be a
# host-mounted bind containing unrelated user files; `chown -R` would
# silently destroy host ownership of those (see issue #19788).
#
# The canonical list of hermes-owned subdirs is the same one the s6-setuidgid
# mkdir -p block below seeds. Keep them in sync if the seed list changes.
actual_hermes_uid=$(id -u hermes)

path_has_symlink_component() {
    path="$1"
    root="${2:-$HERMES_HOME}"
    while [ -n "$path" ] && [ "$path" != "/" ]; do
        if [ -L "$path" ]; then
            return 0
        fi
        if [ "$path" = "$root" ]; then
            break
        fi
        parent="$(dirname "$path")"
        if [ "$parent" = "$path" ]; then
            break
        fi
        path="$parent"
    done
    return 1
}

refuse_symlinked_path() {
    action="$1"
    target="$2"
    if path_has_symlink_component "$target"; then
        echo "[stage2] Warning: refusing $action through symlinked path $target — continuing"
        return 0
    fi
    return 1
}

chown_hermes_tree() {
    target="$1"
    if refuse_symlinked_path "recursive chown" "$target"; then
        return 0
    fi
    chown -R hermes:hermes "$target" 2>/dev/null || \
        echo "[stage2] Warning: chown $target failed (rootless container?) — continuing"
}

tree_has_non_hermes_owner() {
    target="$1"
    find "$target" \( ! -user hermes -o ! -group hermes \) -print -quit 2>/dev/null | grep -q .
}

needs_chown=false
if [ "$(stat -c %u "$HERMES_HOME" 2>/dev/null)" != "$actual_hermes_uid" ]; then
    needs_chown=true
fi
if [ "$needs_chown" = true ]; then
    echo "[stage2] Fixing ownership of $HERMES_HOME (targeted) to hermes ($actual_hermes_uid)"
    # In rootless Podman the container's "root" is mapped to an
    # unprivileged host UID — chown will fail. That's fine: the volume
    # is already owned by the mapped user on the host side.
    #
    # Top-level $HERMES_HOME: chown the directory itself (not its contents)
    # so hermes can mkdir new subdirs but bind-mounted host files keep
    # their existing ownership.
    if refuse_symlinked_path "chown" "$HERMES_HOME"; then
        :
    else
        chown hermes:hermes "$HERMES_HOME" 2>/dev/null || \
            echo "[stage2] Warning: chown $HERMES_HOME failed (rootless container?) — continuing"
    fi
    # Hermes-owned subdirs: recursive chown is safe here because these are
    # created and managed exclusively by hermes (see the s6-setuidgid mkdir
    # -p block below for the canonical list).
    for sub in cron sessions logs hooks memories skills skins plans workspace home profiles pairing platforms/pairing; do
        if [ -e "$HERMES_HOME/$sub" ] && tree_has_non_hermes_owner "$HERMES_HOME/$sub"; then
            chown_hermes_tree "$HERMES_HOME/$sub"
        fi
    done
fi

# --- Immutable install tree ---
# Do not chown runtime code or dependency trees under $INSTALL_DIR back to the
# hermes user. Hosted/container instances keep mutable state under
# $HERMES_HOME (/opt/data) and run with PYTHONDONTWRITEBYTECODE. Keeping
# /opt/hermes root-owned and non-writable prevents an agent session from
# self-modifying the installed source, venv, TUI bundle, or node_modules and
# bricking the gateway. On-demand dependency installs go to PM generations
# under $HERMES_HOME/installs, never into this tree.

# Always reset ownership of $HERMES_HOME/profiles to hermes on every
# boot. Profile dirs and files can land owned by root when commands
# are invoked via `docker exec <container> hermes …` (which defaults
# to root unless `-u` is passed), and that breaks the cont-init
# reconciler (02-reconcile-profiles) which runs as hermes and walks
# the profiles dir. Skip the recursive walk when the tree is already
# owned correctly so warm boots do not rescan huge profile caches.
# Idempotent; skipped on rootless containers where chown would fail.
if [ -d "$HERMES_HOME/profiles" ] && tree_has_non_hermes_owner "$HERMES_HOME/profiles"; then
    chown_hermes_tree "$HERMES_HOME/profiles"
fi

# Always reset ownership of $HERMES_HOME/cron on every boot for the same
# docker-exec/root-write reason as profiles/. The cron scheduler state
# (jobs.json) must stay readable by the unprivileged hermes runtime even
# after root-context maintenance commands or scheduler writes. Skip the
# recursive walk when the tree is already owned correctly (same warm-boot
# gate as profiles/).
if [ -d "$HERMES_HOME/cron" ] && tree_has_non_hermes_owner "$HERMES_HOME/cron"; then
    chown_hermes_tree "$HERMES_HOME/cron"
fi

# Always reset ownership of pairing data on every boot, same docker-exec/
# root-write reason as profiles/ and cron/. `docker exec <container>
# hermes pairing approve …` defaults to uid=0 and writes 0600 root-owned
# approval files that the unprivileged hermes gateway cannot read,
# silently leaving the approved user unauthorized (#10270). The targeted
# data-volume chown above only runs when the top-level $HERMES_HOME is
# mis-owned, so warm boots skip it — this block makes a container restart
# self-heal. Tiny directory (a handful of small JSON files), so even the
# ownership pre-scan is negligible; gated for consistency with profiles/
# and cron/.
if [ -d "$HERMES_HOME/platforms/pairing" ] && tree_has_non_hermes_owner "$HERMES_HOME/platforms/pairing"; then
    chown_hermes_tree "$HERMES_HOME/platforms/pairing"
fi
# Legacy location (pre-consolidated layout).
if [ -d "$HERMES_HOME/pairing" ] && tree_has_non_hermes_owner "$HERMES_HOME/pairing"; then
    chown_hermes_tree "$HERMES_HOME/pairing"
fi

# Reset ownership of hermes-owned top-level state files on every boot.
# The targeted data-volume chown above only covers hermes-owned
# *subdirectories*; loose state files living directly under $HERMES_HOME
# are missed. When those files are created or rewritten by
# `docker exec <container> hermes …` (root unless `-u` is passed) they
# land root-owned, and the unprivileged hermes runtime then hits
# PermissionError on next startup (e.g. gateway.lock / state.db /
# auth.json), producing a gateway restart loop.
#
# We use an explicit allowlist rather than a blanket `find -user root`
# sweep so host-owned files in a bind-mounted $HERMES_HOME are never
# touched — same targeted-ownership contract as the subdir chown above
# (issue #19788, PR #19795). The list mirrors the top-level *file*
# entries of hermes_cli.profile_distribution.USER_OWNED_EXCLUDE plus the
# runtime lock files; keep them in sync if that set changes.
for f in \
    auth.json auth.lock .env \
    state.db state.db-shm state.db-wal \
    hermes_state.db \
    response_store.db response_store.db-shm response_store.db-wal \
    processes.json \
    active_profile; do
    if [ -e "$HERMES_HOME/$f" ]; then
        if refuse_symlinked_path "chown" "$HERMES_HOME/$f"; then
            :
        else
            chown hermes:hermes "$HERMES_HOME/$f" 2>/dev/null || true
        fi
    fi
done

# --- config.yaml permissions ---
# Ensure config.yaml is readable by the hermes runtime user even if it
# was edited on the host after initial ownership setup.
if [ -f "$HERMES_HOME/config.yaml" ]; then
    if refuse_symlinked_path "chown/chmod" "$HERMES_HOME/config.yaml"; then
        :
    else
        chown hermes:hermes "$HERMES_HOME/config.yaml" 2>/dev/null || true
        chmod 640 "$HERMES_HOME/config.yaml" 2>/dev/null || true
    fi
fi

# --- Seed directory structure as hermes user ---
# Run as hermes via s6-setuidgid so dirs end up owned correctly (matters
# under rootless Podman where chown back to root would fail).
#
# Use direct `mkdir -p` invocation (no `sh -c "..."` wrapper) so the
# shell isn't a second interpreter — defends against $HERMES_HOME values
# containing shell metacharacters. PR #30136 review item O2.
as_hermes mkdir -p \
    "$HERMES_HOME/backups" \
    "$HERMES_HOME/cron" \
    "$HERMES_HOME/sessions" \
    "$HERMES_HOME/logs" \
    "$HERMES_HOME/logs/gateways" \
    "$HERMES_HOME/hooks" \
    "$HERMES_HOME/memories" \
    "$HERMES_HOME/skills" \
    "$HERMES_HOME/skins" \
    "$HERMES_HOME/plans" \
    "$HERMES_HOME/workspace" \
    "$HERMES_HOME/home" \
    "$HERMES_HOME/pairing" \
    "$HERMES_HOME/platforms/pairing"

# --- XDG_RUNTIME_DIR ---
# 0700 as dbus requires. It lives in world-writable /tmp under a predictable name
# and holds the display-allocation lock, so it is a security boundary: refuse a
# symlink or a directory someone else owns (chowning that one would hand hermes a
# directory whose creator keeps an fd into it), and chown rather than assume —
# `usermod -u` above does not chown outside the home dir, so a HERMES_UID remap
# would leave it owned by the old uid and every Xfce/dbus/lock open would EACCES.
if [ -n "${XDG_RUNTIME_DIR:-}" ]; then
    xdg_owner=""
    if [ -e "$XDG_RUNTIME_DIR" ]; then xdg_owner=$(stat -c %u "$XDG_RUNTIME_DIR" 2>/dev/null || echo unknown); fi
    if refuse_symlinked_path "create" "$XDG_RUNTIME_DIR"; then
        :
    elif [ -n "$xdg_owner" ] && [ "$xdg_owner" != "0" ] && [ "$xdg_owner" != "$actual_hermes_uid" ]; then
        echo "[stage2] Warning: $XDG_RUNTIME_DIR is owned by uid $xdg_owner (not root or hermes) — refusing to adopt it"
    else
        mkdir -p "$XDG_RUNTIME_DIR" 2>/dev/null || \
            echo "[stage2] Warning: could not create XDG_RUNTIME_DIR $XDG_RUNTIME_DIR (continuing)"
        if [ -d "$XDG_RUNTIME_DIR" ]; then
            chown hermes:hermes "$XDG_RUNTIME_DIR" 2>/dev/null || \
                echo "[stage2] Warning: could not chown XDG_RUNTIME_DIR $XDG_RUNTIME_DIR (rootless?)"
            chmod 0700 "$XDG_RUNTIME_DIR" 2>/dev/null || true
        fi
    fi
fi

# --- Install-method stamp ---
# The 'docker' stamp is baked into the immutable install tree at
# /opt/hermes/.install_method (see Dockerfile), NOT written here into
# $HERMES_HOME. detect_install_method() reads the code-scoped stamp first.
#
# Why we no longer stamp $HERMES_HOME: it is a shared DATA volume, commonly
# bind-mounted from the host (~/.hermes:/opt/data) and sometimes shared with a
# host-side Desktop/CLI install. Stamping 'docker' here clobbered that host
# install's marker, so its in-app updater read 'docker' and refused to run
# 'hermes update'. To heal homes already poisoned by older images, remove a
# stale 'docker' stamp from $HERMES_HOME if one is present (the host install's
# own installer re-creates its code-scoped stamp; a genuine container relies on
# the baked /opt/hermes stamp, so deleting the data-dir copy is safe).
if [ -f "$HERMES_HOME/.install_method" ]; then
    stamped="$(tr -d '[:space:]' < "$HERMES_HOME/.install_method" 2>/dev/null || true)"
    if [ "$stamped" = "docker" ]; then
        rm -f "$HERMES_HOME/.install_method" 2>/dev/null || true
    fi
fi

# --- Seed config files (only on first boot) ---
seed_one() {
    dest=$1
    src=$2
    if [ ! -f "$HERMES_HOME/$dest" ] && [ -f "$INSTALL_DIR/$src" ]; then
        if refuse_symlinked_path "seed" "$HERMES_HOME/$dest"; then
            :
        else
            as_hermes cp "$INSTALL_DIR/$src" "$HERMES_HOME/$dest"
        fi
    fi
}
seed_one ".env" "hermes_cli/data/env.example"
seed_one "config.yaml" "hermes_cli/data/config.yaml.example"
seed_one "SOUL.md" "docker/SOUL.md"

# --- Migrate persisted config schema ---
# Docker image upgrades replace the code under $INSTALL_DIR but preserve
# $HERMES_HOME on the mounted volume. Run the same safe, non-interactive
# config-schema migrations that `hermes update` runs for non-Docker installs,
# after first-boot seeding and before supervised gateway services start.
# Set HERMES_SKIP_CONFIG_MIGRATION=1 for controlled/manual migrations.
if [ -f "$HERMES_HOME/config.yaml" ]; then
    s6-setuidgid hermes "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/scripts/docker_config_migrate.py" \
        || echo "[stage2] Warning: docker_config_migrate.py failed; continuing"
fi

# --- Refresh the dependency generation for this image ---
# Opt-in dependencies (lazy extras, plugin deps) live in PM generations on the
# volume, selected by $HERMES_HOME/installs/*/facts.json. An image upgrade
# replaces uv.lock under that durable selection, so re-resolve it here, before
# any supervised service boots onto a generation built for the previous image.
# On failure (e.g. offline) PM falls back to the image's own environment and
# keeps the extras recorded for the next boot or install. Then collect the
# generations nothing selects any more: no service holds a lease yet, and
# collect_generations keeps anything younger than a day.
s6-setuidgid hermes "$INSTALL_DIR/.venv/bin/python" -c '
from pathlib import Path
from hermes_cli.runtime_state import collect_generations
from pm.environments import install_state_dir
from pm.recovery import refresh_dependencies
from pm.runtime import collect_runtime_generations
root = Path("'"$INSTALL_DIR"'")
print("[stage2] dependency environment:", refresh_dependencies(root))
removed = collect_generations(root) + collect_runtime_generations(install_state_dir(root) / "pm-runtime")
print("[stage2] collected", len(removed), "unused dependency generations")
' || echo "[stage2] Warning: dependency refresh failed; continuing"

# --- Sync bundled skills ---
# Invoke the venv's python by absolute path so we don't need a `sh -c`
# wrapper to source the activate script. This is safe because
# skills_sync.py doesn't depend on any environment exports beyond what
# the python binary's own bin-stub already sets up (sys.path is rooted
# at the venv's site-packages by virtue of running .venv/bin/python).
if [ -d "$INSTALL_DIR/skills" ]; then
    as_hermes "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/tools/skills_sync.py" \
        || echo "[stage2] Warning: skills_sync.py failed; continuing"
fi

# --- Point agent-browser at the pinned Chromium binary ---
# The image's Dockerfile pm-provisions pinned full Chromium into
# $HERMES_RUNTIME_DIR (/opt/hermes/tools) at BUILD time and bakes the
# resolved browser binary path into /etc/hermes/agent-browser-executable-path
# (the layout differs per arch — chrome-linux64/chrome on amd64,
# chromium-linux-arm64/chromium on arm64 — so it is resolved at build time,
# not hard-coded). agent-browser (the runtime CLI Hermes spawns for the
# browser tool) doesn't recognise Playwright's directory layout in its own
# cache scan and fails with "Auto-launch failed: Chrome not found" — even
# though the binary is right there (#15697).
#
# Fix: read the baked path and export ``AGENT_BROWSER_EXECUTABLE_PATH``
# via /run/s6/container_environment so the `with-contenv` shebang on
# main-wrapper.sh propagates it into the supervised ``hermes`` process
# and thence to agent-browser subprocesses.
#
# - Skipped when the user has already set ``AGENT_BROWSER_EXECUTABLE_PATH``
#   (lets users override with a system Chrome install).
# - Quietly skipped when the baked path file is absent (e.g. custom builds
#   that strip the pm tool store).
if [ -z "${AGENT_BROWSER_EXECUTABLE_PATH:-}" ] && \
        [ -f /etc/hermes/agent-browser-executable-path ]; then
    browser_bin="$(cat /etc/hermes/agent-browser-executable-path)"
    if [ -n "$browser_bin" ] && [ -x "$browser_bin" ]; then
        echo "[stage2] Using pinned agent-browser Chromium binary: $browser_bin"
        # Write to s6's container_environment so with-contenv picks it
        # up for all supervised services (the foreground terminal command).
        # Idempotent: each boot overwrites with the current path.
        # Some container runtimes / s6-overlay versions do not create the
        # envdir before cont-init hooks run, so create it defensively.
        mkdir -p /run/s6/container_environment
        printf '%s' "$browser_bin" > /run/s6/container_environment/AGENT_BROWSER_EXECUTABLE_PATH
    else
        echo "[stage2] Warning: baked Chromium binary is missing (${browser_bin:-<empty>}); browser tool may fail"
    fi
fi

echo "[stage2] Setup complete; starting user services"
