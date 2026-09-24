# Handoff — hermes-agent encrypted fork rework (majorissuerep/hermes-agent)

Written 2026-09-24, after the clobber-incident fixes landed at `0e92cc0994`.
This is the full state of the fork for whoever continues the work (future
session, another agent, or a human). Everything below is verifiable against
the repo unless marked otherwise.

## 1. What this fork is

Upstream NousResearch/hermes-agent (base `38c289c014`, v0.21.4) with one
defining feature: **mandatory master-password at-rest encryption of ALL user
state** — plus the hardening that grew around it.

- Repo: https://github.com/majorissuerep/hermes-agent (gh CLI authed as
  majorissuerep). `main` is the ONLY install/update channel: takeover clones
  it and `hermes update` tracks it. Feature work lands via PRs into `main`.
  The old `vault` branch is retired (a stale `vault` shipped the pre-fix
  sealed-.env loader to a real machine). Tag `v1.0.1`, GH release exists.
- Local checkout: `/home/luoman/hermes-agent` (this machine). Worktree clean.
- Version line: `1.0.1` / `2026.9.23`. NOTE the PEP 440 lesson: the first
  release was tagged `1.0.0-vault` which is INVALID PEP 440 — it made
  `uv/pip install -e .` fail with a TOML parse error and BROKE fresh
  installs (fixed in 5e8ea976). Never put suffixes in the version string.

## 2. Core architecture (hermes_security/)

- `vault.py` — scrypt(N=2^15) root key from passphrase; HKDF domain
  separation per purpose; AES-256-GCM envelopes (magic `HRMVAULT\0`), path
  bound into AAD; metadata `.hermes-vault` (salt+verifier, public only).
  **X25519 key slots** (64614fb4): LUKS-style additive slots — master key
  sealed to a PUBLIC key via ephemeral X25519+HKDF+AESGCM; private half
  shown once by `keygen`, human-stored, system never holds it. Either
  credential (passphrase or private key) unlocks the same in-memory master
  key; rotation = re-seal, no re-encryption.
- `sqlite.py` — SQLCipher for ALL DBs, keys path-bound; sqlcipher3
  exceptions rebased onto stdlib sqlite3 so ~57 `except sqlite3.Operational
  Error` sites keep working.
- `io.py` — envelope read/write; plaintext reads REFUSED inside a vaulted
  home (fail-closed); writes refused in a vault-less home.
- `frames.py` — encrypted append-only log frames (`.log`/`.jsonl`).
- `migrate.py` — one-shot home migration (sqlcipher_export, streaming
  SHA-256 fingerprints, per-file-class purposes via `_purpose_for`,
  verified swap) + `repair_clobbered_state` (re-seals plaintext files that
  old-venv processes wrote over envelopes — see §5 incident).
- Gate: `hermes_cli/vault_gate.py` — every state-touching command requires
  the vault (exit 2); read-only bypass set is precise; test escape
  `HERMES_ALLOW_NO_VAULT=1` lives ONLY in tests/conftest.py.
- Crypto-free invariant: update dispatch and the gate must never import
  `cryptography` (Windows self-update lock); import-trace verified.

Credential flows: `HERMES_MASTER_PASSWORD` (env, daemons/non-TTY creation
AND unlock), `--private-key` / `HERMES_VAULT_PRIVATE_KEY` (path to raw
32-byte base64/hex). TTY detection probes `/dev/tty`, NOT stdin.isatty
(curl|bash has piped stdin but a live /dev/tty — refusing on isatty broke
installs; fixed 2b25325a).

## 3. The rest of the fork surface

- **No telemetry**: setup_telemetry no-op, shared-metrics send boundary
  neutralized, OTLP off, defaults send/enabled=false.
- **Updater origin-locked**: upstream sync removed; canonical repo =
  majorissuerep/hermes-agent; update --check stays crypto-free.
- **User-native terminals** (cd29d831): commands run under the USER's login
  shell (passwd-first resolver in tools/environments/user_shell.py; zsh/
  dash/ksh handoff with env-dump persistence; SHELL exported to children).
- **gateway.external_platforms kill switch** (7a9c1172): `false` force-
  disables EVERY external messaging adapter at GatewayConfig parse; LOCAL
  (CLI/TUI/desktop/dashboard) unaffected. Owner's stance: external
  messengers = vulnerability surface. Default true (per-platform opt-in
  preserved for compat); consider defaulting false in a hardened profile.
- **Python 3.14 promoted** (f2404128): requires-python >=3.11; dead
  tflite-runtime transitive dropped via never-true override, replaced by
  ai-edge-litert; watch for 3.13+ `Path.exists()` swallowing EACCES
  (state-db repair + vault_exists already fixed to stat directly).
- **Security scanner gate** (scripts/security_scan.sh): shellcheck, gitleaks
  (+ .gitleaksignore fingerprint baseline), bandit (fork scope),
  pip-audit, semgrep, hadolint, npm audit (5 workspaces), trivy secrets.
  ALL CLEAN at time of writing. Baselines: .gitleaksignore,
  .bandit-baseline.txt.
- **1Password CLI = part of the install** (3b219ae9+f649ab39):
  scripts/install_op.sh (idempotent, curl|bash-safe — NO stdin prompts;
  absolute-path op detection; deb branch proven in a Debian container).
  Scope: WORKFLOW secrets only (op:// references in .env, resolved by the
  upstream onepassword secret source). The VAULT credential is NEVER
  1Password (a machine-readable token would reintroduce the hole the
  asymmetric slots close). Upstream integration already exists:
  agent/secret_sources/onepassword.py + `hermes secrets onepassword`.

## 4. Install / takeover (scripts/takeover.sh)

curl -fsSL https://raw.githubusercontent.com/majorissuerep/hermes-agent/
main/scripts/takeover.sh | bash

Pipeline: stop hermes (ALL venvs, double-sweep for watchdog respawns —
see §5) → move old tree aside (kept) → clone fork branch main (FORK_SRC
env overrides source for offline/429-rate-limited egress; origin re-pinned
to the fork) → venv (uv-first, provisions 3.11; lock-first `uv sync`) →
launcher → op bootstrap → `secure-vault migrate` (prompts master password
via /dev/tty; full tar backup OUTSIDE the home first).

Known ops facts: GitHub anonymous git-over-HTTP is 429-rate-limited from
some egresses (fra edge) — that's why FORK_SRC exists. The pre-migration
tar is PLAINTEXT by design (restore safety); delete after verification.

## 5. The clobber incident (root-caused 0e92cc0994) — READ THIS

A real fresh install migrated a home (12 DBs/17k files), then a leftover
OLD-VENV process (watchdog-respawned gateway or stale --yolo session)
sanitized the sealed `.env` envelope AS TEXT: NUL-strip + plaintext
rewrite destroyed the ciphertext (~188 python-dotenv parse warnings), and
the import-time dotenv load then raised PlaintextStateError so EVERY
command died — including the repair tools.

Fixes (all live-proven in sandbox reproductions):
1. `_sanitize_env_file_if_needed` passes HRMVAULT envelopes through.
2. main.py import-time dotenv degrades to a warning, never a traceback.
3. `secure-vault repair` re-seals clobbered files with the CANONICAL
   purpose per file class (`_purpose_for`) — purpose is in the AAD; a
   generic purpose makes files UNDECRYPTABLE (first implementation had
   exactly this bug, caught by live test). Plaintext DBs are reported,
   never silently wrapped.
4. takeover stop_hermes kills hermes processes from any venv + double
   sweep + survivor warning.

If a machine shows the incident again: run `hermes secure-vault repair`
(survives now), investigate any reported plaintext DBs, re-run takeover
after killing surviving old-venv processes.

## 6. Verification norms (how this repo proves things)

- Tests: `bash scripts/run_tests.sh <paths>` ONLY (never bare pytest;
  per-file isolation, env parity). Current standing results on 3.14:
  hermes_security + config + e2e + sanitizer/dotenv consumers = 401
  passed / 0 failed. Full hermes_state 1403 green. Gateway config suites
  green. Upstream's full ~35k corpus is NOT re-baselined on 3.14 —
  touched-surface-only claim, keep it that way when reporting.
- Shell scripts: bash -n + shellcheck -S warning (both clean) + live
  execution of every reachable branch; deb-only branches proven in a
  Debian container (pass-through sudo stub, stdin closed).
- Live CLI proof pattern: fresh /tmp home + HERMES_HOME + env password,
  drive the real command chain, grep for the contract lines.
- A/B attribution for any upstream-suite failure: clean base worktree at
  38c289c014, same command, compare failure sets (pre-existing vs
  regression). Known pre-existing: filecmp stat-cache flake (fixed in
  fork), timing flakes under 64-worker load, matrix-voice/telegram-video
  env-dependent failures.

## 7. Open items / next steps

1. **The other machine's install**: the incident in §5 happened on a
   DIFFERENT box (laguna). Its ~/.hermes was migrated then clobbered; its
   old gateway (`venv/bin/python ... gateway run`, watchdog + a --yolo
   parent, PIDs 9726/9754/19276 there) must be killed, then
   `hermes secure-vault repair`, then re-verify with `secure-vault status`
   + a plaintext sweep. The fixed takeover (0e92cc0994) handles the kills
   itself — re-running it is the cleanest path.
2. Cut `v1.0.2` (or 1.1.0) once the other machine is green: the clobber
   fixes postdate v1.0.1.
3. Consider `gateway.external_platforms: false` as the fork default
   (owner leans hostile to external messengers; currently default true).
4. Consider a `hermes doctor`-style preflight that runs the §5 repair
   check automatically when a vault exists + plaintext state is found.
5. Upstream rebases: upstream base is now behind; when syncing, the fork
   merge surface is hermes_security/*, the gate, seven config-read sites
   (config.py, config_effective.py, config_backups.py, auth store, env
   tokenizer, banner, gateway status), env_loader sanitize, io read/write
   guards, terminal user_shell, gateway config lockdown, updater slugs.
   NEVER take an upstream change to _sanitize_env_file_if_needed or
   load_hermes_dotenv without re-checking the envelope passthrough +
   degrade-on-error semantics.
6. Memory/skill: session knowledge is in the hermes-agent-fork-development
   skill (uv at ~/.local/bin/uv; scripts/run_tests.sh; HERMES_ALLOW_NO_
   VAULT only in conftest). Update it if the workflow changes.

## 8. Quick command reference

    hermes secure-vault status | migrate | unlock | lock | repair
    hermes secure-vault keygen | add-key --public-key F | remove-key --slot N | slots
    env creds: HERMES_MASTER_PASSWORD=... or HERMES_VAULT_PRIVATE_KEY=/path
    scanner gate: bash scripts/security_scan.sh   (ALL CLEAN required)
    tests:        bash scripts/run_tests.sh <paths>
    fresh install: see §4 command; offline: FORK_SRC=/path takeover.sh
    incident:     §5 — repair survives everything now

— end of handoff —
