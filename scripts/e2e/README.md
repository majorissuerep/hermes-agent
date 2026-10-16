# Reproducible encrypted lifecycle acceptance

Linux scenarios start in a new **digest-pinned Ubuntu 24.04 container**; macOS
scenarios use fresh hosted macOS 15 VMs on Apple Silicon and Intel. Each has an
empty Hermes home and cold PM tool store. The Linux host only exports committed Git objects,
controls Docker/Kubernetes and collects sanitized evidence. It never installs or
runs Hermes, unlocks a real home, or mounts personal files into a test.

| Scenario | Contract |
|---|---|
| `fresh` | Full cold source installer, mandatory vault, live delegated task |
| `update` | OLD fork commit → exact candidate, one update unlock, retained state |
| `broken-files` | Damaged tracked source repaired; unrelated untracked work preserved |
| `dirty-install` | Existing plaintext settings, secret, memory and obsolete launcher survive reinstall and encryption |
| `reinstall` | Rerun the source installer after deleting a CLI module and its runtime interpreter; preserve unrelated local work |
| `upstream-install` | Install an actual pinned upstream Hermes first; preserve its settings, credentials, conversation and pre-encryption snapshot when moving to this fork |

Each case exercises real encrypted settings, credential resolution, session DB
integrity, memory, skills, cron and attachments. It mutates state and restores a
real snapshot and full ZIP backup, mutates settings, and restores through public
`hermes import`. Upstream migration must restore both pre-encryption backup forms.
It reproduces supported clobbered-file shapes, runs public vault
repair, exports a verified plaintext recovery archive, re-encrypts a copy, and
compares semantic state. Pre-encryption upstream snapshots must also restore.
A real LLM delegates a config read, checks updates and writes
settings without another passphrase. A second session resumes the restored
conversation and recovers a random marker from its history. An independent CLI
without authority must remain locked. No API or application imports are mocked.
An agent saying "done", an existing file, or a green process exit is not proof.

## Local Docker or Kubernetes

Use a dedicated, spend-capped OpenAI-compatible gateway credential:

```sh
python3 scripts/e2e/container_lifecycle.py \
  --backend docker --scenario all --baseline OLD_SHA --candidate NEW_SHA \
  --evidence /path/to/new-evidence --credentials-stdin
```

Pass credential JSON on stdin from a trusted secret source, never argv or chat.
The fields are `provider` (`litellm`), `model`, `base_url`, `api_key`, and optionally
`api_mode`. Alternatively configure `E2E_LLM_PROVIDER`, `E2E_LLM_MODEL`,
`E2E_LLM_BASE_URL`, `E2E_LLM_API_KEY`, `E2E_LLM_API_MODE` for the coordinator.
It consumes these before spawning any children. Installer/PM workers receive no
LLM credential. The synthetic vault passphrase is generated inside each test,
kept in driver memory and entered through a PTY; never in env, argv or artifacts.

For pods use `--backend kubernetes`. By default the coordinator creates and
removes its own namespace. `--namespace DEDICATED_TEST_NAMESPACE` instead uses an
existing test namespace and deletes only its own uniquely named pods. Do not
point this at an application's namespace. No service-account token, hostPath,
Docker socket, host network, persistent volume or shared Hermes cache is mounted
in the test pods. Each pod is limited to 4 CPUs, 8 GiB RAM and 40 GiB ephemeral
storage. At most two scenarios execute concurrently.

The gateway must be reachable **from the container**. The inner driver checks
DNS/TCP/TLS before installation. Missing credentials and unreachable gateways
fail; they never become skipped-green live coverage. Do not expose a private
gateway publicly just for testing.

## Reproducibility and isolation

- Ubuntu's OCI digest and the immutable APT snapshot are pinned in
  `container_lifecycle.py` / `container_bootstrap.sh`. Evidence includes the
  resolved image ID and installed OS package versions. The minimal image's CA
  bootstrap uses a checksum-pinned Ubuntu package, not host certificates or
  disabled TLS checks; subsequent APT access uses HTTPS and signed metadata.
- The source archive contains an independent, fsck-verified Git object store,
  not a live checkout, alternates, host Git configuration or untracked files.
  It retains complete baseline→candidate ancestry. Both ends must differ.
- Archive transport, bootstrap, orchestrator and inner harness are SHA-256-bound.
  Run the committed candidate's coordinator, not an uncommitted replacement.
- Every leg provisions its own pinned Python/dependencies/tools through PM. No
  application runtime or state is preseeded in the OS image or reused across legs.
- The public installer/updater uses exact-SHA local Git transport. No fake release
  is published. Standalone updates must prompt exactly once; delegated local
  commands must not prompt. A new independent resumed session unlocks once.
- Dirty-install checks compare actual pre-existing values before fixture seeding,
  and again after restoration and live config changes; mere file existence fails.
- The coordinator verifies receipts, deletes each container/pod, then writes the
  aggregate. Failed, missing, cancelled, cached or partially executed cases never
  pass. Cleanup failure is acceptance failure, not a warning.

## GitHub Actions

`.github/workflows/encrypted-lifecycle.yml` runs six Linux container legs and
twelve native macOS VM legs (six scenarios × two architectures), with
`fail-fast: false`, maximum parallelism two per platform job, and a fail-closed
aggregate gate. Select `platform: all` for cross-platform qualification;
Linux-only or macOS-only runs qualify only that subset.
There is no schedule. Trigger manually or push to `e2e/encrypted/**`.

Configure:

- Secret `E2E_LLM_API_KEY` (dedicated short-lived gateway key, never personal OAuth).
- Variables `E2E_LLM_BASE_URL` and `E2E_LLM_MODEL`.
- An exact candidate and distinct OLD fork baseline.

Public gateways use Docker on standard GitHub-hosted Ubuntu coordinators. For a
LAN-only gateway, select `private_runner: true` or `e2e/encrypted/private/**`.
Those coordinators use isolated pods and the label `hermes-encrypted-container`.
Set `E2E_KUBERNETES_NAMESPACE` to a dedicated test namespace. The one-job ephemeral
coordinator needs only pod create/get/list/watch/delete and pod-exec access there;
it does not need application secrets, host mounts or cluster administration.
Replenish consumed runners; never label a personal Hermes workstation.

## Evidence and regression tests

Keep the whole evidence directory: aggregate `acceptance.json`, per-scenario
`isolation.json`, sanitized logs, and the inner `receipt.json`. Together they bind
source/image/harness identities, model/provider, prompt counts, independent
delegation/resume oracles, retained-state hashes, restore and cleanup verdicts.
Upload only evidence, never test homes, vault backups, Git bundles or credentials.
Actions retains evidence for 14 days.

```sh
scripts/run_tests.sh tests/scripts/e2e/
```

These fast regression tests exercise real subprocess/PTY/state paths, source
export and negative receipt controls. They have no live credentials and are not
a substitute for the selected cold container/native-VM/live-LLM receipts.

The remote-backends CI lane additionally runs `test_container_docker_live.py`
against a real Docker daemon with `--fail-on-skip`. It verifies source transport,
no host mounts or inherited model credential, and actual container removal.

## Provider conversation qualification

`test_provider_conversation.py` uses the real agent, tool execution, compaction,
SQLCipher persistence and resumed history against strict local HTTP protocol
oracles. The shared `provider_matrix.py` maps every bundled provider identity to
its supported Chat Completions, Responses, or Anthropic Messages formats. Every
route runs normally and with an initial rate limit plus a summary-backend outage.
System-prompt stability, complete tool pairs, bounded tokens/calls, preserved
user turns, committed compaction and a random continuity anchor are required.

`.github/workflows/live-providers.yml` runs that same conversation probe against
one explicitly selected real connection across Linux and both macOS architectures.
Choose all supported API modes for that connection. Configure a dedicated
`LIVE_<connection>_API_KEY` secret and `LIVE_<connection>_BASE_URL` / `MODEL`
variables; LiteLLM can reuse the dedicated `E2E_LLM_*` settings. Missing credentials
fail before the matrix; never upload personal subscription tokens. Qualification
receipts identify the exact commit, harness, endpoint host, model, protocol, native
OS/architecture, compaction, token totals, resume and disposable-home cleanup.
One gateway/model does not qualify another vendor or a subscription account.

The cold `fresh` lifecycle leg also runs all three LiteLLM conversation formats
when LiteLLM is selected. Its strict receipt must include every live dialect,
compaction, encrypted persistence and resume result. This permits real gateway
qualification inside private-network pods without exposing the gateway publicly.

See the [reliability contract](../../website/docs/developer-guide/reliability.md).
