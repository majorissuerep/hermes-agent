---
title: Encrypted tooling and upstream maintenance
---

# Encrypted tooling and upstream maintenance

The operator outcome is a local agent that can maintain its own installation and
use native, Codex and Claude children after one human unlock. Configuration,
credentials, history and owned outputs must remain encrypted. A successful
command exit alone does not establish that outcome.

## Baseline and boundaries

The starting fork revision is `1815472adf69eb16cc201c0d9ac4c7e69f5fa51b`.
On 2026-11-02 upstream main was fetched at
`8bf1b7ef75e27f1e9eec969f4beec47f06519fdb`: 6,894 upstream commits after
the shared base `eb75627faf532fa6ffbdae59a0d14b0b385fc780`. The unfinished
`sync/upstream-20261022` and `ci/gitea-primary-20261028` branches are inputs to
review, not accepted product evidence.

The local command layer owns management; the native agent owns conversations;
`hermes_security` owns encryption and temporary unlock authority; terminal and
process helpers own child lifetimes. External coding agents retain their own
authentication and permission systems. Remote execution never inherits a local
vault grant. Desktop, messaging, ACP, inbound management and remote session
hosting remain outside this fork. No change authorizes a public deployment.

## Observable contracts

- An unlocked local owner can read and change configuration and run its supported
  management commands without another prompt. Independent or foreign-profile
  commands remain locked. Unreadable state is an error, not an empty substitute.
- A child can invoke the owning Hermes installation. Closing that child leaves
  the parent usable; ending the owner revokes grants derived from it.
- Codex and Claude command integrations use noninteractive invocation and do not
  persist an additional plaintext conversation. Their permission controls remain
  effective; refusal must be reported rather than retried without confinement.
- Ordinary CI has a single reproducible command and bounded resources. Failed,
  cancelled, timed-out or missing required checks never qualify a merge. Native
  macOS, live-provider and cold lifecycle evidence remain distinct.
- Upstream inputs are pinned and reviewed. Ports preserve encrypted writers,
  profile isolation and stable prompt prefixes. Importing an upstream feature
  does not qualify it or restore removed services.

## Design and stopping conditions

Reuse the public CLI and existing session capability instead of adding a model
tool or a management service. Prefer one shared verification driver over duplicate
forge-specific scripts. Port relevant upstream changes in reviewable increments;
a wholesale merge currently includes more than two million added lines and would
cross removed product boundaries.

Ordinary POSIX children redeem grants through private, owner-only Unix sockets.
The managed coding-agent runner uses private kernel FIFOs because a real Codex
workspace sandbox rejected even Unix socket connections with networking disabled.
Only FIFO nodes and an empty lock inode exist on disk. Keys and requests remain
in process memory and pipe buffers; client responses are correlated and bounded.
The runner adds only this transport directory to Codex's writable paths. Profile
writes outside the workspace require the caller's explicit `--add-dir` selection.

Compression reanchors conversation identity without replacing unlock ownership;
old grants for running children expire with the same owner. Delegated and nested
authorities validate their parent when redeemed. Interpreter key-FD handoffs
validate any inherited session grant before adopting the key, preserving that
revocation chain. Ordinary handoffs carry one selected vault owner. Maintenance
must explicitly name additional already-unlocked homes; it never enumerates all
cached keys or drops unreadable members from PM's existing shared dependency union.
An explicit lock detaches all grants backed by that vault before stopping their
transports; a later unlock cannot revive the old tokens or revoke another vault.

Nested executor output may contain a capability unknown to the parent's exact
secret registry. The output redactor recognizes the grant structure, including
one JSON wrapper, before optional redaction preferences. It removes the bearer
token while preserving diagnostic routing metadata.
Managed runners use the existing profile-aware environment builder and remove
launch-profile residue and native executor tokens before selecting vendor
credentials. A profile's endpoint accompanies its API key; a CLI-owned login
keeps its own routing. Codex receives the key through its documented per-run
`CODEX_API_KEY` interface, rather than relying on `OPENAI_API_KEY` to override
saved CLI authentication.

Outgoing MCP stdio servers receive the owning profile path, with an explicit
server override still honored. Their environment remains filtered and does not
inherit vault grants, key FDs or passphrases. MCP diagnostics use the existing
encrypted log frames, and the pipe reader retains the profile's redaction scope.
An unreadable log stops startup with a repair remedy; a write failure is reported.
Offline repair retains legacy diagnostics and enables subsequent encrypted appends.
Log migration, legacy-backup import and explicit recovery export retain line
terminators and partial final lines; JSONL record framing is unchanged.
The native parent-death supervisor keeps its kernel-pipe diagnostics and filtered
environment without receiving unlock material.

The consequential assumptions are that the public command inherits the correct
installation and grant, and that external executors preserve the grant through
their own shell boundaries. Real child commands against synthetic encrypted
homes falsify those assumptions. Loopback/provider fixtures establish protocol
behavior only. An actual native host/account run is required for a live claim.
Keep missing evidence pending. Retain failing receipts and preserve original
homes and revision identities for rollback; never decrypt a home in place to
make a test pass.

## Evidence and review

The November 2 investigation retained these negative results: encrypted xAI
credentials were invisible to its availability probe; compression changed the
conversation ID without reanchoring authority; login shells dropped executable
locations; nested key-FD handoffs could renew authority after owner close; and
unregistered nested grants escaped output redaction. A lock/unlock cycle also
revived old session tokens until permanent lock revocation was added. Behavioral regressions
reproduced each failure before its corresponding fix. No plaintext fallback,
global networking opt-in or permission bypass was used to resolve them.

Live host evidence at `ea337ed0db3b24d3a29ee533d23c0e78e9ade192` used Linux
6.6.114.1 WSL2 x86_64, Codex CLI 0.166.0 and Claude Code 2.1.316 with their own
existing logins with invalid ambient executor tokens deliberately supplied.
Both executed an owning-Hermes configuration read and write;
independent command refusal, owner-close revocation, ciphertext and synthetic-home
removal were independently checked. Codex used `workspace-write` with networking
disabled. Claude used narrowly selected Bash permissions. These are CLI integration
receipts, not cold-install or every-provider qualification.

Native Linux protocol evidence includes the real public finite-chat/delegation
path, a managed Hermes chat subprocess, encrypted SQLCipher history, two native
children calling the management CLI,
real PTY keyboard/transcript round trips and Docker source transport with no host
mounts or inherited model credential. The host's native macOS coverage is pending.
A full native owner also launches a managed Hermes chat child, which reads the
owning encrypted configuration; after its exit the parent reads that same state.
Five provider requests and two SQLCipher sessions independently establish this
round trip. Synthetic profiles must first receive a real PM dependency selection.
The public MCP stdio scenarios completed four checks; seven pre-existing expected
failures for unsupported image results and empty metadata remain unqualified.
Separate real-SDK regressions verify owning/explicit child homes, filtered unlock
material, encrypted/redacted diagnostics, write failure and legacy-log recovery.
The six cold lifecycle/live-gateway scenarios require their existing dedicated
credentials and disposable environments; they remain pending here.

The repository-wide security scan was executed and failed: existing shell lint
findings, synthetic-secret detections, static audit findings, dependency advisories
and npm advisories remain visible. The committed patch's secret scan was clean.
The PM-built test environment's audit reported 65 advisories in seven existing
locked packages; no dependencies or lockfile were changed. Do not present this as
a clean security audit or weaken directory permissions to satisfy a scanner's
incorrect recommendation to replace owner-only directory mode 0700 with 0644.

The task PR records the final candidate and exact-head verification results.
The fork maintainer owns review after an unlock regression, plaintext-state
finding, child ownership failure, upstream boundary change or CI coverage drift.

## Upstream and CI operation

Refresh the source inventory without executing upstream code or changing the
checkout:

```sh
review_dir="$(mktemp -d)"
python3 -m scripts.ci.upstream_review --fetch --output "$review_dir/upstream.json"
python3 scripts/ci/verify.py --plan --event pull_request
python3 scripts/ci/verify.py --base origin/main
```

The inventory records source/base SHAs, graph distance, affected local/excluded
areas and a bounded candidate list. It creates a new output exclusively and
does not count file parity as qualification. This increment ports upstream
`6fa171ce5423457fa46da227562ddea766c9ef69` and
`35c2783b6ab7419209cad9f746f64d215f400b60` login-PATH behavior, with the background
spawn wiring from `29fef14fbf3501ac02140bc34ea339d64ff650ce`. Foreground/background
tests execute actual tools from absolute and relative PATH entries. The larger
upstream merge remains unaccepted.
The profile-path port from `9d8efb3504d75810080bca994ed8a5a2bbf137c3` is adapted
to the fork's encrypted MCP diagnostics and existing filtered environment.

One Woodpecker quality flow invokes the shared verification command:
PM-pinned bootstrap, blocking lint guards, lock consistency, canonical Python
tests, icon freshness, Ink/workspace checks, generated skill pages, agent-facing
documentation indexes and documentation build. The common blocking command also
retains the existing added-line critical-source/dependency-bound patterns and
tracked infographic policy; these narrow guards are not a comprehensive security
audit. Workers are bounded and
test retries are disabled. The driver pins and prints input revisions and refuses
dirty or replaced checkouts. The clone explicitly disables partial fetching,
bounds history to 256 commits and fetches the target branch; a missing merge base
fails closed. The first live run exposed that the clone plugin's default partial
mode selected a shallow tree-filtered checkout despite depth 0. It was cancelled.
A subsequent full-history fetch hit the service's HTTP 504 deadline. Neither run
counts as qualification; bounded history retains the reviewed diff without an
unbounded archive transfer through the ingress.
The pinned clone image requires `home: /root` so its initial and secondary Git
commands use the same credential file. Clone options must remain in `settings`:
Woodpecker 3.13 excludes steps with an `environment` block from trusted-plugin
credential injection. Failed runs exposed both conditions; an isolated image
probe verifies candidate and target SHA after the authenticated fetch.
The verifier copies the immutable checkout from the shared NAS onto the pod's
temporary disk before provisioning and testing, avoiding repeated network-file
imports. It remains bounded to two CPUs, 6 GiB RAM and 8 GiB temporary storage,
within the existing namespace limits; the temporary copy is removed on exit.
Native SSH fixtures pin their generated host key in a private trust file through
the real clients, and only replace a container bearing the same fixture ownership
label. Repeated runs do not write test keys into the operator's SSH trust store.

The private source is `unpossible/hermes-agent` on the existing Gitea service;
the public mirror is `majorissuerep/hermes-agent` on GitHub. The sole GitHub
entrypoint, `quality.yml`, requires the exact revision's latest verification
from the existing `hermes-ci` identity and supplies native host/daemon evidence.
Older green statuses, other workflows and other status writers cannot qualify a
revision. Historical GitHub entrypoints remain disabled.
The status reader traverses history pages before selecting the newest trusted
result: the deployed Gitea 1.22 reverses the documented `highestindex` ordering.
Real HTTP regressions cover an older green followed by pending, failure or
success on a later page.

A separate main-only Woodpecker mirror flow depends on verification. It first
pushes the ordinary `mirror/qualification` branch, waits for GitHub Actions'
unchanged `All required checks pass (v2)` application-bound check, then
fast-forwards public main. It checks source-main identity before and after
publication, requires public-main ancestry, and reads back the resulting SHA.
It never forces a push or synthesizes a passing status. Source drift, divergent
history, failure and missing evidence stop publication. The source credential is
read-only; the SSH deploy key can write only the existing public repository.
Woodpecker supplies these credentials only to push events in the allowed image,
not pull requests. No vault key or real model credential is used by ordinary CI.

The aggregate GitHub gate retains native Linux/macOS tests, real terminal input,
real SSH/Docker, installer protocol/stamp checks and Docker/shell lint. Full
production cold installs and live-provider acceptance remain separate evidence.
Repository protection/visibility and public exposure decisions remain unchanged.
Acceptance requires the final source revision's green verification and matching
public-main readback; configuration alone is not a completed mirror run.
