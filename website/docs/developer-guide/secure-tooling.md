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
The transport directory contains FIFO nodes, an empty lock inode and a
credential-free command bound to the owning installation. Keys and requests remain
in process memory and pipe buffers; client responses are correlated and bounded.
Both ends validate the open descriptor's FIFO type, owner and private mode.
A replaced response file, symlink, directory or shared FIFO is refused before a
key frame is written, and repairing the node permits the next valid request.
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

Source/module launches also provide a private temporary Hermes command when no
published installation launcher exists. This prevents a PM environment's console
script or another ambient checkout from becoming the agent's self-call target.
The command contains only executable/source locations, bootstraps the real owning
PM selection, and disappears with its process or managed call. No grant or key is
stored in the command. Native nested calls and vendor shell calls use this same
installation-bound launcher implementation.

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

The xAI model migration uses the existing encrypted, transactional config reader
and atomic writer. It scans canonical primary and auxiliary model slots, retains
provider namespaces, comments and unknown fields, and makes an encrypted backup.
A locked/damaged config fails without changing bytes; an operator selection made
after preview is retained rather than overwritten.

## Evidence and review

The November 2 investigation retained these negative results: encrypted xAI
credentials were invisible to its availability probe; compression changed the
conversation ID without reanchoring authority; login shells dropped executable
locations; nested key-FD handoffs could renew authority after owner close; and
unregistered nested grants escaped output redaction. A lock/unlock cycle also
revived old session tokens until permanent lock revocation was added. Behavioral regressions
reproduced each failure before its corresponding fix. No plaintext fallback,
global networking opt-in or permission bypass was used to resolve them.

A private-branch probe found that the FIFO server assumed its response path
remained a private FIFO. Four node-replacement cases failed before the repair.
The server now validates the open inode before writing a credential frame and
refuses replaced nodes without stopping subsequent valid owner requests.

Fresh root-container checks also exposed an ambient-console-script self-call,
plaintext xAI migration I/O, an unhandled pre-filter Python 3.11 tar API, and a
sensitive-write approval gap for the real one-component `/root` home. Focused
regressions retain the failures on the base. Older bootstrap Python uses a
contained extractor that rejects escaping paths/links, special members, duplicate
files and forward hard links; it strips archive ownership and privilege bits.
Current Python continues to use its stdlib data filter.

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
The refreshed November 3 inventory pins upstream
`6059d35108afd0afc2455dbe5e6bdac999933d1e`. Its child-transcript reservation and
first-turn initialization fixes (`13e0ce98012998a207932b2d1475162371053600` and
`6059d35108afd0afc2455dbe5e6bdac999933d1e`) use this fork's native SQLCipher DB.
Queued-child progress is relayed to the existing TUI event consumer only after
reservation succeeds. A failed reservation closes the unpublished child and
leaves the parent usable; first-turn prompt/config persistence still completes.

The availability/prefix fixes from `9b839d815e`, `7aca8caeaf`, `b9e983665c` and
`c49a94bee1` expire discovery memos with their profile-scoped verdicts. Live
Tool Search can discover and invoke a recovered MCP tool while the conversation's
schema bytes stay fixed. Pin restores in the same process preserve that boundary,
including the synthesized bridge during an outage. Explicit reload retains its
existing boundary. The ports keep the fork's session and sandbox filters.

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
The check process runs under a real child reaper, including when a test's parent
is killed. A separate diagnostic pod with `sleep` as PID 1 retained dead zombies
and could not qualify those process-disappearance contracts. Its negative results
are retained. PM's verified download-cache output is forwarded as data to
synthetic-home provisioning, without sharing profile state or unlock authority.
The pinned clone image requires `home: /root` so its initial and secondary Git
commands use the same credential file. Clone options must remain in `settings`:
Woodpecker 3.13 excludes steps with an `environment` block from trusted-plugin
credential injection. Failed runs exposed both conditions; an isolated image
probe verifies candidate and target SHA after the authenticated fetch. The
clone now uses a repository-only read-only SSH key through the plugin's
documented settings, with a host key read from the running Gitea instance.
The first actual token-expiry failure occurred between the initial fetch and
the target-branch fetch after a slow checkout. This separate repository binding
preserves both authenticated fetches without changing the shared CI bot's
interactive OAuth login or giving PR verification the mirror write key. An
environment-based binding was rejected by Woodpecker's image-filter rules;
those rules remain enforced. The actual pinned clone image fetched both refs,
verified the candidate and target SHAs, and received Gitea's explicit refusal
of a write dry run using that same read-only key.
CI pods use the private cluster SSH service. The host-facing SSH route was
unreachable under the CI namespace's default-deny egress policy. The checked-in
`scripts/ci/woodpecker-clone-egress.yaml` allows only this repository's `git`
clone pods to reach Gitea's SSH pod port; verification pods remain outside that
rule. Public ingress, DNS and repository protections are unchanged.
The verifier copies the immutable checkout from the shared NAS onto the pod's
temporary disk before provisioning and testing, avoiding repeated network-file
imports. Four Python workers are bounded to four CPUs, 6 GiB RAM and 16 GiB
temporary storage, within the existing namespace limits. CPU/memory reservations
remain two CPUs and 4 GiB. The two-worker live run's measured throughput projected
beyond the existing two-hour job limit; worker width changes without narrowing
tests or enabling retries. Concurrent PM fixture environments reached about
7 GiB before the CLI suite finished, then the pod was evicted at its original
8 GiB limit. The storage limit leaves room for four workers within the existing
20 GiB per-container ceiling, while its reservation
remains 2 GiB. The temporary copy is removed on exit.
The bounded in-house timeout is three hours so the complete Python, frontend and
documentation checks fit together. The former two-hour setting projected too
close to the full suite's measured runtime. Expensive management contracts are
split by native management versus executor integration so neither file deadline
hides the remaining cases; their assertions and zero-retry policy are unchanged.
Native SSH fixtures pin their generated host key in a private trust file through
the real clients, and only replace a container bearing the same fixture ownership
label. Repeated runs do not write test keys into the operator's SSH trust store.
The root-run pod exposed six pre-existing container-path tests probing the real
`/root/.hermes`. A disposable root container reproduces all six on the base;
temporary mirror paths preserve their assertions without weakening the home-I/O
guard or changing production safety code. The other root-path fixtures also pass.
The full run also exposed two scheduler tests timing job preparation and thread
scheduling instead of the blocked operation. Their contracts now synchronize on
the finalizer and exercise fence refusal while the side effect still holds its
lock, followed by successful revocation after release. Home-prefix normalization
uses a temporary multi-component home and retains explicit short-prefix refusal
checks; production approval rules are unchanged. These repairs require a fresh
full run and do not turn the failing run into passing evidence.

The next full run exposed a metrics fixture reading before deferred SQLite writes
had settled. A held write transaction reproduces the missing session row on the
base. The test's flush now waits for export workers and drains deferred writes
before inspection; the fork's disabled collection policy is unchanged. The same
run timed out the updater process fixture during sustained disk-write stalls.
Its POSIX virtual environments now link the immutable interpreter rather than
copying three large binaries for each case; their site-packages and checkout
transitions remain isolated. The original file deadline and zero retries remain
in force. These negative runs do not qualify the new candidate.

A later full run exceeded the restart fixture's 15-second process guard before
its first output. That guard included cold imports and durable initialization;
the failure alone did not identify a runtime hang. The fixture now gives each
fresh interpreter a bounded 60-second outer guard with slow-child stack capture.
Its existing five-second operation window waits for actual completion publication
instead of polling and exiting when the clock expires. All restart stages use
one encrypted synthetic home and inherited one-shot key FDs, with independent
ciphertext and replay/acknowledgment assertions. The task PR records whether the
complete zero-retry run confirms both startup and completion contracts.

The same run exposed a native readiness mismatch: the final reply reached the
screen before the owner released its live-turn fence, while the UI already
reported ready. A global model choice in that interval could receive the
existing deferred acknowledgment, then be lost when the operator quit before
another turn. The final reply now carries the owner's running state; the UI
holds busy and pending notices until the authoritative idle snapshot. The public
PTY contract waits for current composer readiness, rejects a deferred
acknowledgment for its idle global write, then verifies ciphertext and an
independent owner's actual model/reasoning request. Mid-turn picks keep their
existing next-turn deferral.

A subsequent full run timed out a cold SQLCipher acquisition before a stubbed
archive writer started. These response-framing tests now prepare and retain a
real encrypted registry store before running the job, matching their earlier
owner-fixture intent. They assert that the stub agent receives that exact store
and that its file is ciphertext. Production acquisition deadlines and the
separate timeout, late-close and fail-closed contracts are unchanged. A quick
native rerun passed before this repair, so it does not disprove the CI failure;
the complete new-head run remains the acceptance evidence.

The private source is `unpossible/hermes-agent` on the existing Gitea service;
the public mirror is `majorissuerep/hermes-agent` on GitHub. The sole GitHub
entrypoint, `quality.yml`, supplies native host/daemon evidence. Private
verification is enforced by the publisher on its existing private network;
hosted GitHub runners need neither forge access nor a private-source credential.
Older green statuses, other workflows and other status writers cannot qualify a
revision for publication. Historical GitHub entrypoints remain disabled.
The status reader traverses history pages before selecting the newest trusted
result: the deployed Gitea 1.22 reverses the documented `highestindex` ordering.
Real HTTP regressions cover an older green followed by pending, failure or
success on a later page.

A separate main-only Woodpecker mirror flow depends on verification. It first
requires the exact revision's latest verification from `hermes-ci`, pushes the
ordinary `mirror/qualification` branch, waits for GitHub Actions'
unchanged `All required checks pass (v2)` application-bound check, then
rechecks private verification before fast-forwarding public main. It checks
source-main identity before and after publication, requires public-main ancestry,
and reads back the resulting SHA.
It never forces a push or synthesizes a passing status. Source drift, divergent
history, failure and missing evidence stop publication. The source credential is
read-only; the SSH deploy key can write only the existing public repository.
Woodpecker supplies the publishing bindings only to push events in the allowed
Python image. The clone image has a separate repository-only read-only SSH key
on clone events; PR verification cannot receive the mirror write key. No vault key
or real model credential is used by ordinary CI.

The first actual hosted GitHub gate failed because the private forge hostname
has no public DNS address. Moving its private-proof check to the publisher avoids
a new runner, VPN, signing key or public ingress. Both gates remain mandatory:
real-Git regressions refuse failed, pending or missing private proof before any
qualification push, and refuse proof that regresses while native checks run.
The unchanged GitHub aggregate still requires both native host jobs to pass.

Another zero-retry run exposed HTTP-fixture interference in the Codex host
binding tests. Earlier picker cases started usage workers that could outlive
their mocks and enter a later case's recorder with a different synthetic key.
The picker cases now keep real Codex usage workers within the owning fixture,
mock unrelated provider usage, and wait for worker settlement before restoring
HTTP/profile state. They also assert the usage requests' host and credential;
the later fallback test keeps its exact key assertion. Production credential
selection and background behavior are unchanged.

The first SSH-backed full run also exposed an incomplete offline guard in the
lazy-secrets update probe: it rewrote HTTPS and SCP-style Git URLs but let
`ssh://` reach the network and hit its 30-second subprocess deadline. The guard
now covers all three forms. Real-Git regressions use a transport recorder to
prove refusal before any SSH process starts; the CLI dispatch checks, original
deadline and verification pod's network fence remain unchanged.

An I/O-stalled full run exposed two custody-test timing assumptions: a one-second
delay did not establish that the launcher had started, and a writer's timer could
expire before the build returned. The custody cases now wait for the actual node
and detached writer, record PID/start-time identities, then assert that both are
dead when the build or its termination returns. A controlled cold-launch delay
reproduces the former signal failure and passes with readiness synchronization.
The macOS tree-walk contract runs on macOS, without changing `sys.platform`.
Its recorder forwards the native `ps` output unchanged and observes a descendant
sample before releasing the node; Linux uses its real subreaper.

The same run recorded about 50-60% full I/O stall on its worker, followed by
unrelated 600-second updater-file timeouts. Those failures remain negative
evidence. Verification requires the operator-maintained node label
`theincompetent.dev/hermes-verification=true`; clone and publication use the same
eligible pool after an actual clone also stalled on the original worker.
Clone remains a plugin with image-filtered credentials. Qualify a worker with
native pressure observations, a disposable write/fsync probe, sufficient
allocatable resources, and a complete
zero-retry run. Remove the label when pressure, eviction or repeated deadline
failures invalidate that evidence; qualify a replacement before reapplying it.
Kubernetes pressure taints, test deadlines, resource limits and required checks
are retained. Worker selection grants no credential or network authority.

Clone also disables the plugin's default LFS fetch/checkout: `git lfs ls-files`
reported no managed files at the reviewed revision, while the actual clone spent
minutes scanning the shared volume for LFS checkout. Ordinary Git checkout and
the full verifier remain required. Adding LFS-managed assets requires reviewing
this setting and qualifying their actual hydrated content.

A zero-retry run also caught the five-step stale-compression-holder fixture
timing out before its blocked summary began. It now uses the existing
summary-entry barrier from its sibling isolation cases. The actual blocked-work
timeout stays bounded by that helper's shorter idle/ceiling, while real
SQLCipher/worker preparation has its own readiness guard. After transferring
the durable lease, the test joins the old worker before asserting unchanged
cooldown, replacement-holder lease and transcript. A controlled preparation
delay reproduces the former failure and passes with the barrier. Production
compression budgets and fencing are unchanged.

The aggregate GitHub gate retains native Linux/macOS tests, real terminal input,
real SSH/Docker, installer protocol/stamp checks and Docker/shell lint. Full
production cold installs and live-provider acceptance remain separate evidence.
Repository protection/visibility and public exposure decisions remain unchanged.
Acceptance requires the final source revision's green verification and matching
public-main readback; configuration alone is not a completed mirror run.
