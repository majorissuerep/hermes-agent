---
title: Encrypted tooling and upstream maintenance
---

# Encrypted tooling and upstream maintenance

The operator outcome is a local agent that can maintain its own installation and
use native, Codex and Claude children after one human unlock. Configuration,
credentials, history and owned outputs must remain encrypted. A successful
command exit alone does not establish that outcome.

## Delivery status

The integration milestone is **held for qualification**. The behavior below is
the intended contract and the implementation under review, not a claim about the
installed package or current main. Independent native checks found custody,
session-binding and owned-state persistence defects in unmerged candidates.
Repairs and red-on-base contracts are prepared. Acceptance still requires the
final candidate's required checks, guarded merge/publication and a non-author's
fresh-main replay. Private security evidence stays with the restricted review.

The [delivery order](#delivery-order-and-gates) and
[follow-up backlog](#explicit-follow-up-backlog) define the stopping point.
The wider hidden reasoning controller remains deferred; the separate opt-in
formal-checkpoint consumer and held Goal prototype do not establish its delivery.
Unknown swap protection and unresolved image-library advisories also prevent a
whole-system privacy or advisory-closure claim.

## Baseline and boundaries

The starting fork revision is `1815472adf69eb16cc201c0d9ac4c7e69f5fa51b`.
On 2026-11-02 upstream main was fetched at
`8bf1b7ef75e27f1e9eec969f4beec47f06519fdb`: 6,894 upstream commits after
the shared base `eb75627faf532fa6ffbdae59a0d14b0b385fc780`. The unfinished
`sync/upstream-20261022` and `ci/gitea-primary-20261028` branches are inputs to
review, not accepted product evidence.

A read-only upstream refresh on November 4 observed and fetched
`9302ba9e212c63ba24cdf1f47f26b47bcd3b042e`, 584 commits beyond that
reviewed input. This is a new review input, not an accepted port. **UPSTREAM-02**
tracks the relevant local CLI/agent/MCP fixes; removed product surfaces stay out.

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
- A trusted worker's one-shot key descriptor belongs to the exact live session.
  A stale session cannot select a sibling's authority. Installer shell children
  request fixed owner-executed stages and receive completion receipts only.
- Owned skill indexes and usage audit records use the existing authenticated
  storage formats. A locked or damaged file is preserved and reported, never
  treated as permission to append plaintext or overwrite it with an empty value.
- Retained SQLCipher connections, cursors and transaction admission remain bound
  to their original unlock and borrowed session. A live sibling or later human
  unlock cannot revive a revoked handle. Cleanup remains possible after revocation.
- Terminal overflow, large tool/MCP results and owned browser captures use
  authenticated encrypted artifacts. Existing file tools recover text in the
  owning profile; vision consumers authenticate image bytes and stage derivatives
  only in a proven RAM workspace. Refused persistence reports its limitation.
  Ciphertext copied from another artifact or a different log must fail
  authentication for the requested output path.
- Request-failure diagnostics and delegated task manifests, live views and
  summary spills use authenticated encrypted persistence. Their original home,
  unlock and session remain bound through callbacks and finalization. A later
  unlock cannot revive old work; unavailable persistence is reported without
  creating a plaintext substitute.
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

Ordinary local children receive operation-scoped grants through private Unix
sockets or kernel FIFOs. These grants never export a permanent vault key. The
owner executes bounded management requests and approved native Hermes commands
under the exact profile/session scope. Native commands preserve stdin, streamed
stdout/stderr, exit status, cancellation and owner lifetime. Interactive or
arbitrary executable/interpreter escapes are refused. Trusted native Hermes
children receive keys only through an explicitly inherited one-shot kernel FD.

Managed coding agents use FIFOs because a real Codex workspace sandbox rejected
Unix sockets with networking disabled. Endpoints live in short, owner-only IPC
directories outside idle-pruned profile scratch. They contain socket/FIFO nodes
and an empty lock inode; payloads stay in memory and kernel buffers. Both ends
validate descriptor type, owner and private mode. Replaced response files,
symlinks, directories and shared FIFOs fail closed. The source-bound command has
its own active runtime directory and lifetime. Vendor filesystem access does
not need a writable encrypted profile: management writes happen in its owner.

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
secret registry. The output redactor recognizes structured grants and typed v2
tokens even after registry churn, revocation or a fresh process. This boundary
precedes optional redaction preferences and preserves diagnostic metadata.
Managed runners use the existing profile-aware environment builder and remove
launch-profile residue and native executor tokens before selecting vendor
credentials. A profile's endpoint accompanies its API key; a CLI-owned login
keeps its own routing. Qualification must observe the selected native API
endpoint and credential on the wire; merely exporting `CODEX_API_KEY` does not
prove the CLI selected that authentication path.

Retained vault keys use kernel-locked, wipeable custody. Refused memory locking
fails the unlock; explicit lock, cache clear and normal finalization wipe that
custody. Native borrowed objects check their original lifetime before key use,
including retained object references. Human re-unlock creates distinct owner
custody. An already exported immutable Python snapshot cannot be erased by
revoking a grant. KDF/crypto copies, SQLCipher memory temporaries and ordinary
process buffers remain outside the locked arena.

`hermes manage apply` with `{"op":"status"}` reports requester scope separately
from the responding resource, actual retained-key protection, memory limits,
swap state and protected-workdir evidence. It never treats an observation as
human consent or certifies unknown swap/crash storage. A RAM-backed filesystem
alone does not establish whole-process confidentiality. The current WSL host's
swap is active and its backing encryption could not be verified.

`security.protected_work_dir` selects transient storage for managed vendor work
and formal checks. When absent, the per-user runtime directory is only a
candidate. Creation requires an actual never-swapped RAM proof; an explicitly
invalid location never falls back. Normal completion removes the private task
directory. Existing general `cache/scratch` retention does not make that cache a
protected workspace. Native executor persistence and owner-death cleanup need
their own qualification; non-persistence flags alone are insufficient evidence.

Private terminal and tool outputs are stored beneath `artifacts/terminal-output`
and `artifacts/tool-results`; owned browser images use `artifacts/browser`.
Oversized browser accessibility trees use `artifacts/browser-snapshots`. Their
truncation footer names the authenticated host path for the existing file tools,
including when task execution uses an outgoing backend. Paging, search and
element references survive without mounting or uploading private page text.
A failed encrypted save is explicitly reported; no plaintext cache is created.
An explicit image path can be inspected with `hermes artifacts view PATH` or
`/artifacts view PATH`. This authenticates and decodes in memory, then renders a
bounded grayscale text preview (at most 120 columns by 60 rows). It creates no
plaintext viewing file. Vision tools still receive the image pixels. Private
`MEDIA:` references provide that slash-command hint instead of an external link
to ciphertext. General configuration and credentials are outside this reader.

Delegation keeps its existing `cache/delegation` layout, including populated
legacy `delegation_cache` homes. Task live views and full summary spills can be
read through the authenticated file tool. `/agents` and the terminal subagent
view authenticate their retained child's log before presenting its tail. The
control manifest has a separate authenticated purpose and is unavailable to
generic file tools. These private caches are not mounted into remote backends.
The compact live view is atomically resealed per event, with a 64 MiB limit;
the encrypted child history retains the full conversation. Refused or failed
live persistence is marked unavailable while bounded result delivery survives.
An old batch with no original storage custody cannot acquire a later unlock.
Explicit offline repair handles plaintext and older encrypted cache formats;
runtime readers never import those formats implicitly.

API failure diagnostics retain their masked request/error JSON under `sessions`
using the existing encrypted state format. The error notice names the encrypted
file and deliberate offline forensic export. Missing profile metadata, revoked
custody or a failed write withholds the dump and preserves the original API
failure. An explicitly selected profile without its own vault cannot borrow an
initialized ancestor's diagnostic storage.

Older private outputs in `cache/terminal-output`, `cache/spillover`,
`cache/screenshots` and `browser_screenshots` remain visible to the read-only
plaintext scan. Explicit vault repair seals them in place and hardens their
permissions; it preserves their authenticated legacy paths. Raw old images are
refused by runtime readers until repaired. Package-manager and other public
caches remain excluded. This change does not erase old bytes from SSD media.
The same explicit repair covers the old `browser-snapshot-<hash>.txt` files in
`cache/web` and `web_cache`. Ordinary public web extracts in `cache/web` keep
their existing cache policy. A planted symlink at a private destination is
refused and retained for inspection; its target is never written through.

The existing initial migration creates a documented plaintext recovery archive
outside the home, and deliberate offline exports also produce plaintext. These
operator recovery operations are distinct from normal encrypted runtime output.
Inspected computer-use image/element caches and ordinary user-image staging
still have plaintext writers, as does selected remote browser scratch. Browser
profiles and remaining generated media require an inventory. Those ports and
their native-backend qualification remain explicit follow-up work; these changes
do not certify every SSD byte or the host's swap and crash storage.

A running old owner retains its old implementation after a source update. New
operation-only clients report `owner_restart_required` with restart/resume
guidance; repeated provider login cannot upgrade that process. The frozen
trusted-FD updater handoff remains supported. It does not authorize generic
key redemption or imply that an old owner has acquired v2 protections.

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

This section retains earlier diagnostics and their limits. Separate ciphertext,
wire and process observations establish those particular contracts; they do not
replace non-author acceptance of the actual merged revision. Historical receipts
cannot qualify a later candidate.

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

Historical live host evidence at `ea337ed0db3b24d3a29ee533d23c0e78e9ade192` used Linux
6.6.114.1 WSL2 x86_64, Codex CLI 0.166.0 and Claude Code 2.1.316 with their own
existing logins with invalid ambient executor tokens deliberately supplied.
Both executed an owning-Hermes configuration read and write;
independent command refusal, owner-close revocation, ciphertext and synthetic-home
removal were checked through separate observations. Codex used `workspace-write` with networking
disabled. Claude used narrowly selected Bash permissions. These are CLI integration
receipts, not current-candidate, cold-install or every-provider qualification.

Earlier native Linux protocol evidence includes the real public finite-chat/delegation
path, a managed Hermes chat subprocess, encrypted SQLCipher history, two native
children calling the management CLI,
real PTY keyboard/transcript round trips and Docker source transport with no host
mounts or inherited model credential. The host's native macOS coverage is pending.
A full native owner also launches a managed Hermes chat child, which reads the
owning encrypted configuration; after its exit the parent reads that same state.
Five provider requests and two SQLCipher sessions separately establish this
round trip on that earlier revision; current-candidate chat/delegation acceptance
requires its own isolated replay. Synthetic profiles must first receive a real PM dependency selection.
The public MCP stdio scenarios completed four checks; seven pre-existing expected
failures for unsupported image results and empty metadata remain unqualified.
Separate real-SDK regressions verify owning/explicit child homes, filtered unlock
material, encrypted/redacted diagnostics, write failure and legacy-log recovery.
The six cold lifecycle/live-gateway scenarios require their existing dedicated
credentials and disposable environments; they remain pending here.

A later isolated Linux replay exercised actual managed native chat and a native
delegated conversation, including child and parent terminal self-calls: two
authenticated model requests for the managed child and five for the delegated
conversation, with two SQLCipher sessions. All functional contracts passed, but
private goal text was found in the delegation cache. An intentionally rejected
API request separately exposed plaintext request diagnostics. Both failures
remain negative evidence; their repairs require an exact-candidate replay and
independent acceptance. Neither a successful exit nor redacting API keys alone
establishes encrypted conversation storage.

The repository-wide security scan was executed and failed: existing shell lint
findings, synthetic-secret detections, static audit findings, dependency advisories
and npm advisories remain visible. The committed patch's secret scan was clean.
The initial PM-built environment reported 65 advisories in seven locked packages.
A later, separately qualified dependency refresh reduces this to 39 advisories
in Pillow 12.3.0. It updates certifi, MCP, PyJWT, urllib3, setuptools, Mistral and
OpenTelemetry using releases older than the existing 14-day quarantine, without
metadata overrides or quarantine exceptions. A fresh PM environment passes its
dependency-consistency check; this remains a failing security audit. Pillow 13's
fixes are blocked by the declared browser-harness and fastembed constraints.
A November 4 audit of the unchanged baseline Node lock found 17 affected package
nodes (three high and 14 moderate). An aged, compatible refresh of Undici,
Vitest/Vite and Zod reduces that result to 12 moderate nodes and zero high or
critical nodes. Every new resolved identity was at least 14 days old. A fresh
install, complete dependency-tree validation and all workspace checks passed
locally. The remaining Minimatch/ESLint development-chain findings require a
major-version compatibility review, and the currently identified fix is still
inside the age window. This remains an incomplete security audit; separate
website and retired-surface lockfiles were outside this Node audit.
Do not weaken owner-only directory permissions to satisfy a scanner's incorrect
recommendation to replace mode 0700 with 0644.

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
An earlier November 3 inventory pins upstream
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

The latest source-only inventory pins upstream
`8112254f8a934626032579cae967b25b7795836d`: 7,462 commits ahead and 7,091
changed files relative to the fork's recorded shared base. The narrow issuer
compatibility port from `1dadda835d9e67a11758481cf123c821dc2538db` accepts the
single optional slash on a root issuer, preserving path-sensitive matching,
discovery checks and origin restrictions. The refreshed MCP 2.2 SDK supplies
the matching helper directly. Actual Mistral SDK wire tests also found that
transcription hints require `context_bias`; generic `prompt` was rejected by
both the previous and refreshed SDK. The port sends a vocabulary/phrase hint
without changing other transcription providers.

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


Private run 26 completed 41,780 Python cases, but its ten-scenario device-login
file exceeded the 600-second file budget after two cases and failed the run.
Those scenarios now run in cohorts of one or two, with common wire/state oracles
kept in one helper. Every scenario still creates its own encrypted PM home and
uses the public CLI, real SDK and local HTTP fixture. Polling, refresh, issuer and
resource validation, credential non-disclosure and failed-write preservation
assertions are unchanged. The file limit, CLI limits and zero-retry policy are
unchanged. A local four-worker run passed all ten cases, with a maximum cohort
time of 91 seconds. Complete new-head CI remains required; this does not turn the
failed run into acceptance evidence.


## Revocation and protected-child acceptance follow-up

Held-out checks reproduced four failures on the earlier source candidate:
retained vault objects could write after local lock/cache clear, and a live
trusted child could keep writing through retained or cached custody after its
owner closed. The repaired custody rechecks borrowed lifetime, wipes on
revocation and refuses stale handles. Human re-unlock creates independent owner
custody. Encrypted SQLite temporary storage also refuses legacy file-backed
settings, without rewriting the saved option.

Native positive management tests run in a private Linux user/mount namespace
with verified ramfs. The qualification helper checks an inherited parent
namespace descriptor before any mount operation and requires zero skipped
management, vendor callback and supervision cases. It changes no host mount or
swap setting. A run without proven RAM may test refusal behavior, but skipped
positive vendor cases do not qualify execution. macOS has no protected vendor
scratch proof backend in this increment.

That pass exposed an owner/client fixture mismatch: operation-only dispatch
selects executors in the owner's environment, while an old fixture changed only
the client's PATH. Fixtures now bind synthetic executors in the owner and use
different rejecting client executors. The authority never accepts caller PATH
as executable-selection authority. A separate owner-death case exposed retained
RAM scratch after process cleanup; the supervisor now owns descriptor-pinned
cleanup after all descendants end. Namespace refusal, replacement-directory
preservation and descriptor closure have separate behavioral contracts.

The auxiliary TTL test also raced its ten-millisecond real-time window during
logging. Its replacement controls time and checks active state just before
expiry, then full cache eviction exactly at the deadline. Failed earlier runs
remain negative evidence. Final private/native qualification and source/public
main equality are still required before completion.

## Refined protected-workflow backlog

The operator's November 3 refinement prioritizes reliable encrypted native
Hermes, Codex, Claude and outgoing MCP integration, followed by protected,
decomposable workflows whose assumptions and deductions can be checked. The
controller owns bookkeeping and verification. The main model keeps its ordinary
working interface and receives concise consequential findings; this does not
add a reasoning toolset or rewrite past prompts, memories or tool schemas.

The bounded delivery window starts at **2026-11-03 21:08 UTC** and ends at
**2026-11-04 07:08 UTC**. Work uses GPT-6.1 Sol at high effort and Claude Opus 5.5
at high effort, with approximately equal delegated work packages. The
coordinator owns integration, evidence reconciliation, merge and publication.
Model agreement is not independent evidence. A non-author owns each milestone's
independent acceptance, using fresh environments and the actual merged SHA.

### Intent, boundaries and measurable completion

The primary outcome is successful authorized work from an already-unlocked local
session: no second unlock for owned children, no foreign-profile credential
borrowing, no loss of encrypted state, correct cancellation and recoverable
failures. "Flawless" is an acceptance objective expressed by executable
contracts; it is not a claim that all possible defects have been ruled out.

Sensitive prompts, claims, premises, evidence, proof artifacts, checkpoints,
credentials and operational traces must use the existing encrypted writers.
Plaintext processing needs a protected transient environment. RAM can be swapped
or dumped: report the actual memory/storage posture, and fail closed when a
required protection cannot be established. Do not put vault keys or passwords
in argv, environment, persistent state or logs. Public source code and
regenerable public tool caches retain the established exclusions. Do not change
host swap, disk-encryption, deployment or public-exposure decisions implicitly.

Measure completed tasks separately from verifier acceptance, rejection and
abstention. Include every failed attempt, timeout, retry, elapsed time, model
identity, token/cost evidence and human intervention in the denominator. Record
the input, source, checker and environment identities. A green old revision,
stale log stream or compiler exit code cannot certify the current outcome.

### Delivery order and gates

| Milestone | Backlog and owner | Completion evidence | Independent test after merge |
| --- | --- | --- | --- |
| **M0: qualify encrypted integrations** | **INT-01** native/self-called Hermes, Codex and Claude; **MCP-01** device login, refresh, issuer selection and scoped persistence; **CI-01** minimal private verification, native GitHub gates and automatic public mirror. Integration owner: coordinator; independent acceptance: a non-author for each boundary. | Real encrypted homes, real native processes and CLI entry points, observed routing, ciphertext inspection, parent survival, revocation, cancellation and failure cleanup. All required checks green for the candidate; source/public main readbacks match. Current status: held; custody, exact-session and owned-I/O repairs require final qualification. | A non-author starts from fresh main, with separate homes/runtime roots and held-out lifecycle scenarios. Exercise logged-in external CLIs, MCP refresh/failure recovery, install/update/restore and stale-capability rejection. Foreign-host or unavailable-credential cases remain explicitly unqualified. |
| **M1: protected workflow vertical slice** | **WF-01** immutable contract and receipt; **ASSUME-01** encrypted, source-linked assumptions; **VERIFY-01** hidden checker routing; **LEAN-01** one reviewed formal obligation; **OPS-01** logged-in operational status, diagnosis, cancellation and recovery. Wider integration is deferred until M0 acceptance; the existing opt-in consumer is not the hidden controller. | A concrete local workflow decomposes into dependency-bound work, checks required premise review/attestation state and currency, runs an available validator, returns actionable feedback, and resumes from an encrypted checkpoint without replaying committed side effects. No new core model tools or persistent plaintext work artifacts. Formal evidence is conditional on the reviewed specification and premises; it does not establish premise truth. | Swap implementation/test ownership. Use fresh main and adversarial held-out cases: false/missing/stale premise, changed statement, contradictory premises, forged receipt, `sorry`/unexpected axiom, checker unavailable, interrupted child and revoked parent. Failure blocks certification and produces a corrective backlog item. |
| **M2: broaden only from evidence** | **EVID-01** primary-source evidence register; **ORCH-02** larger DAGs and bounded parallel execution; **KB-02** source-change invalidation and selective human review; **ADAPT-02** additional executor/backend qualification. | Extend a proven consumer, retain model-neutral contracts and per-adapter evidence. Quantified task gains require an applicable published result or an honest Hermes-specific measurement; do not relabel filtering as better task completion. | Independent regression and outcome review after each subsequent major merge; no blanket provider, operating-system or model-independence claim. Deferred work stays in this backlog if the delivery window cannot accommodate its full merge and acceptance cycle. |

M0 also includes **SEC-01**, operation-scoped child authority with trusted native
key handoff; **SEC-02**, measured memory and temporary-storage protection; and
**PROC-01**, supervised foreground executor lifetime and permission preservation.
These are qualification blockers when their contracts fail. Independent review
confirmed custody, exact-session and owned-state persistence defects; the
prepared repairs remain unmerged. Reproduction, repair and acceptance remain
separate steps. Security-report handling follows
`SECURITY.md`; this backlog does not publish private report details.

M1 must retain one checker backend and one active controller. The separately
authorized opt-in formal-checkpoint consumer asks the main model for a bounded
checkpoint and uses an auxiliary evidence reviewer. That is an explicit opt-in
interface choice, not a mostly hidden implementation. The default architecture
still aims to keep bookkeeping behind the harness. A locally tested Goal
prototype checks reviewed obligations before the existing completion judge;
hold that separate controller for reconciliation, then reuse common checker
identity, policy and receipts through a narrow adapter. Existing kanban and
process supervision retain decomposition and lifetime ownership. Automatic
claim extraction, broad knowledge-graph synchronization and general parallel
proof search belong to M2 until a smaller consumer is qualified.

The retained Goal prototype is excluded from M0 and is not a product command in
that candidate. In its separate local branch it accepts a finite Boolean obligation through the existing
operator `/goal verify` command. It retains the original obligation, source
digests, separate premise attestations, a review identity bound to the exact
generated Lean module, and encrypted attempt receipts. `/goal verify show`
exposes the exact statement and canonical variable binding for human review;
ordinary status remains concise. At most eight atoms, sixteen premises and 128
syntax nodes keep consistency and proof work bounded. Unknown, refuted or stale
premises pause completion. The existing completion judge runs only after these
prerequisites pass, and source currency is checked again before accepting DONE.
This is a local completion gate; it does not automate claim extraction or prove
an empirical premise true. Its pinned Lean process has a 15-second wall guard,
one thread and a 512 MiB allocator limit, which is not a total RSS guarantee.
Only this bounded completion slice is implemented locally. The wider M1
decomposition, side-effect recovery and host-admission contracts remain pending;
existing kanban functionality alone does not establish their combined acceptance.

The integration cutoff is **00:08 UTC**: start exact-candidate qualification
early enough to allow private CI, native GitHub gates, the source merge and an
independent main acceptance cycle before the delivery deadline. Unqualified
later features stay explicitly deferred; a deadline does not authorize bypassing
checks. Roll back an unmerged increment by reverting only its task-owned commits;
post-merge defects require a corrective PR and the same checks.

The cutoff has passed. Only repairs to concrete M0 acceptance failures continue;
new workflow features remain deferred. Each major merge is followed by a fresh
non-author acceptance run before that milestone is described as complete. A
stacked feature may be prepared earlier, but does not skip that acceptance step.

### Consequential assumptions and review triggers

| Assumption | Evidence or falsifier | Owner and response |
| --- | --- | --- |
| RAM-backed work avoids persistent plaintext. | Active swap, dumpability, filesystem backing and container memory controls must be established. Unknown backing encryption does not prove protection. | Memory owner; refuse sensitive protected-workspace creation when the required posture is missing. |
| A child capability preserves the owner's encryption boundary. | A synthetic child can perform its authorized operation, cannot redeem a permanent key through its general capability, and loses mediated access when the owner closes. | Authority owner; block M0 until native and failure-path tests pass. |
| More checking improves the intended result. | Published gains on other models and harnesses motivate the mechanism. Transfer to GPT-6.1 Sol or Opus 5.5 in Hermes is unestablished; changed domains or extra compute can invalidate it. Filtering precision alone does not establish task completion gains. | Harness owner; retain conditional claims and record outcome limits, using a bounded local comparison only when existing evidence cannot settle a material choice. |
| The formal obligation preserves the user's meaning. | Human-reviewed original and formal statement identities agree; a changed statement or load-bearing unreviewed premise invalidates acceptance. | Obligation reviewer; keep fidelity or premise truth unknown until evidence exists. |

**Historical failed M0 checkpoint:** candidate `0777eea6ba` had 35 task commits.
Actual private run 26 processed all 3,895 Python files: 41,780 tests passed and
525 skipped, but the MCP device-flow file exceeded its unchanged 600-second
guard. The run is failed, so frontend/docs, current native GitHub qualification,
merge and public-main publication remain pending. The isolated reproduction
passed, which does not disprove the CI failure. Native diagnostic stacks locate
repeated PM toolchain preparation before MCP starts; the suite-owned machine and
fresh encrypted-profile repair is awaiting qualification. Its exact CI blocking
syscall remains unknown. Other manual/native evidence and its limits are recorded
above; none substitutes for the missing gates.

**Native integration follow-up:** actual Codex 0.166.0 and Claude Code 2.1.316
were exercised against synthetic encrypted homes and loopback model protocols
in a private RAM filesystem namespace. Codex's default read-only sandbox
exposed a client-lock write attempt; opening the existing validated regular
lock read-only preserves exclusive locking and permits mediated encrypted
writes without broadening the sandbox. A separate native probe showed that a
profile API key also needs explicit Codex `model_provider`, `forced_login_method`
and `openai_base_url` configuration. These arguments contain no credential. The
no-profile-key case leaves native CLI-owned login and routing in control. These
are native CLI/protocol observations, not current paid-provider, subscription
login, macOS or exact merged-revision acceptance.

### Explicit follow-up backlog

| Priority and item | Required result | Acceptance and owner |
| --- | --- | --- |
| **P0 SEC-03: qualify custody and session repairs** | Fixed owner-executed installer stages return receipts only; trusted workers require their exact live session; ordinary and outgoing MCP/remote environments exclude installer control authority. | Integration owner; exact-head private/native gates, then non-author fresh-main key-custody, revocation-between-preparation-and-adoption, parent/sibling survival and failure cleanup checks. The earlier confirmed failures remain negative evidence. |
| **P0 SEC-04: qualify owned-state encryption** | Seal derived skill indexes and cron usage audit appends in their canonical authenticated formats, preserving existing records and refusing unavailable/damaged state. | Integration owner; ciphertext and authenticated roundtrip checks, migrated-record preservation, locked/damaged non-mutation and public cron/self-call replay with no owned plaintext residue. |
| **P0 SEC-05: qualify retained database and private output custody** | SQLCipher handles keep their original session across cached reads, cursor fetches, transactions and reopened connections. Terminal/result archives and browser captures remain encrypted, recoverable and revocable; old private caches participate in explicit repair. | Security and integration owners; final exact-head gates, then non-author fresh-main tests for stale handles, sibling isolation, authenticated text/image recovery, real RAM staging, unavailable custody and unchanged data on refusal. Pre-fix native failures remain evidence; unit/browser fixtures do not establish live browser qualification. |
| **P0 SEC-06: qualify failure diagnostics and delegation persistence** | Seal request dumps, task goals/control manifests, live views and summary spills while preserving public readers, original home/session custody, legacy recovery and bounded results when storage fails. Generic file tools cannot read the control manifest. | Integration and security owners; retain the actual native negative receipts, then independently test ciphertext and authenticated roundtrips, nested missing profiles, wrong-home reads, lock/re-unlock, delayed callbacks, failed initial storage, concurrent writers, CLI/TUI consumers and explicit legacy repair. Replay the successful five-request native delegation and the separate rejected-request privacy case on the final candidate, then fresh main after merge. |
| **P0 MEDIA-02: finish private media ports and recovery lifecycle** | Port computer-use screenshot/element caches, ordinary private image staging and selected remote capture scratch; inventory browser profiles and generated media. Keep deliberate offline migration/export archives explicit and preserve consumers. This blocks a whole-toolchain encrypted-storage claim. | Tool/lifecycle owners; isolate and reproduce each private-byte writer, use encrypted persistence or proven RAM, then run real-backend routing, residue and recovery checks. Independently repeat after the corresponding main milestone. Explain deliberate plaintext recovery archives and define their lifecycle without claiming SSD erasure; keep unknown host swap protection separate. |
| **P0 HOST-01: transient-memory admission** | Establish swap, hibernation and crash-storage protection for each supported protected host; report unknown evidence honestly. Retained-key locking and RAM-only files do not protect every Python, crypto or SQLCipher buffer. | Operator supplies host guarantees or a native deployment proves the required posture. A non-author tests refusal and recovery. Full protected-workflow acceptance remains pending while admission is unevaluated. No implicit host setting changes. |
| **P0 DEP-01: Pillow advisory closure (external blocker)** | Upstream browser-harness and fastembed metadata currently prevent installing the aged fixed Pillow through normal consistency checks. | Dependency owner tracks compatible upstream releases and rechecks at the next eligible release/quarantine boundary; then fresh PM lock/environment, applicable advisory closure and real browser/image consumers. Preserve the 14-day quarantine and all existing tooling; do not force incompatible metadata. |
| **P0 DEP-00: qualify the aged refresh** | Land the separately prepared security dependency and SDK refresh after its exact-head checks. Local audit reduces 65 advisories in seven packages to the 39 Pillow advisories recorded above. | Integration owner; fresh PM environment, actual SDK protocol checks, full required gates and independent fresh-main consumers. Local test counts alone do not establish publication. |
| **P0 DEP-02: qualify Node fixes and close the remaining chain** | Qualify the aged high-severity fixes and their compatible lock, then resolve the 12 remaining Minimatch/ESLint development-chain findings when the fixed version clears quarantine and its major-version parents are compatible. | Dependency owner; fresh install, dependency-tree validation, complete workspace and real terminal checks, then non-author fresh-main replay. Retain the baseline audit and rejected young resolution as evidence; no incompatible metadata or age-policy exception. |
| **P0 INT-02: reconcile the native follow-ups** | Include read-only Codex management, profile API routing and cached-login preservation, sticky-profile authority binding, and every concrete remote-client spawn boundary. Exercise real native managed chat and delegated AIAgent conversations with terminal CLI self-calls as well as configuration-only commands. | Integration owner; exact-head private/native checks followed by independent fresh-main CLI, observed model/tool requests, encrypted parent/child history and ciphertext checks. Diagnose failed or incomplete probes before accepting this milestone. A partial author test run cannot qualify it. |
| **P1 NATIVE-02: remaining host and lifecycle evidence** | Qualify protected workspaces, native cancellation/reaping, install/update/restore and logged-in vendor routes on each supported native host. | Non-author host owner. Keep Linux protocol, macOS, synthetic API login and real account evidence distinct; unavailable dedicated credentials remain pending. |
| **P1 UPSTREAM-02: refresh the reviewed input** | Review newer upstream changes against the pinned input and port only concrete consumers within this fork's encrypted local scope. | Integration owner records the fetched revision, port/defer decision and encrypted behavior proof. A newer upstream HEAD alone neither qualifies a port nor authorizes removed services. |
| **P1 HARNESS-02: one coherent formal contract** | Preserve the authorized opt-in consumer and hold the separate Goal controller. Reuse verified PM tool identity, source/review identities, protected scratch, conditional status and aggregate attempt/cost receipts through one checker boundary. | Harness owner; compare existing consumers and failure contracts, then merge only a fully qualified bounded increment. A future default hidden controller requires a concrete consumer; opt-in checkpoint visibility is not evidence that this preference has been fulfilled. |
| **P2 ORCH-02 / KB-02** | Broaden decomposition and source invalidation through existing kanban/goal interfaces only after a concrete consumer needs them. | Independent outcome review after each major merge; report actual task resolution, cost and human effort rather than verifier counts alone. |

### M1 behavioral contracts

- **Assumptions:** retain original statement/span, provenance, owner, applicability
  scope, evidence revision, review state and dependencies. Distinguish observed
  fact, source-supported claim, human requirement, model inference and open
  premise. New evidence invalidates dependent receipts rather than silently
  preserving a green result.
- **Formal boundary:** keep the reviewed natural-language obligation and exact
  formal statement linked by immutable identity. Lean checks a deduction under
  its premises. It does not establish real-world truth or semantic fidelity.
  Reject placeholders, unexpected axioms, statement substitution and unapproved
  compiler-trusting shortcuts. Use satisfiability/witness checks where supported;
  otherwise consistency and formalization remain unknown.
- **Orchestration:** dependencies, budgets, cancellation, failure, intentional
  inactivity and recovery are explicit states. Keep provider failure, execution
  failure, checker failure and missing evidence distinct. A stale or unavailable
  checker cannot pass a gate. Validate the current artifact again at an action's
  side-effect boundary.
- **Operational self-service:** expose useful status and repair actions through
  existing CLI/session consumers, with scoped authority and redacted output.
  Closing or cancelling one child must preserve its parent and unrelated work.
  Persistent private state uses existing encryption and atomicity contracts.
- **Independent acceptance:** an author or another model's agreement is not the
  acceptance oracle. Prefer native execution, authoritative source data,
  independent checker replay and a non-author's held-out scenarios. Record each
  merged SHA and resulting observation; a failed post-merge gate opens a
  corrective increment and prevents a completed-capability claim.

### Evidence informing the design

Existing results motivate selective external checking and a small persistent
controller as a design hypothesis, with material limits. In formal mathematics, the October
[h1 study](https://arxiv.org/html/2610.35962v1) reports the same Opus 4.8 improving
from 63/100 FATE-X problems in native Claude Code to 73 with its prompt/tools and
83 with h1. That is useful strong-backbone evidence, not a general task-success
guarantee; the paper also describes reruns of some baseline failures.

[VERGE](https://arxiv.org/html/2601.20055v2) reports GPT-OSS-120B HLE accuracy
14.2% to 30.5% on its evaluated subset, using a hybrid formal/model-checking
pipeline and substantially more calls than ordinary chain-of-thought. Its
compute controls and soft-check coverage matter. [VeriFin](https://arxiv.org/html/2608.10213v1)
primarily demonstrates selective financial-claim filtering, with rejection and
abstention; that is distinct from increasing solved questions.

[SkillsBench v4](https://arxiv.org/html/2602.12670v4) reports substantial curated
guidance gains across several model/harness combinations, including GPT-5.5, but
selected healthy/backfilled scored trials and partial-credit metrics must not
be presented as all-attempt reliability. These studies do not establish gains
for GPT-6.1 Sol or Opus 5.5 in Hermes. Transfer to these executors remains an
explicit assumption until qualified.

Lean's [official proof-validation guidance](https://lean-lang.org/doc/reference/latest/ValidatingProofs/)
distinguishes proof validity from statement meaning and treats unreviewed
AI-generated proof code as potentially malicious. A pinned checker, isolated
execution and independent final validation therefore belong to the harness,
not to a promise that compilation alone certifies an answer.

The existing experiments are the primary basis for implementation choices.
Small Hermes-specific comparisons are a fallback for consequential transfer
assumptions and acceptance of the actual package; they are not a substitute for
the published evidence or a pretext to postpone the integration work.
