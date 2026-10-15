# Encrypted lifecycle VM acceptance

`.github/workflows/encrypted-lifecycle.yml` assigns each scenario a **fresh Ubuntu
24.04 hosted VM**. It has no schedule and no skipped-green credential path.

| Scenario | Contract |
|---|---|
| `fresh` | Full source installer, mandatory vault, live delegated task |
| `update` | OLD fork commit → exact candidate, one update passphrase, preserved state |
| `broken-files` | Damaged tracked source + untracked work, real updater repairs code and preserves state |
| `dirty-install` | Prior config/memory + stale launcher, full reinstall preserves previous state |

Every scenario checks real encrypted config/secret/session/memory/skill/cron/attachment
readers, database integrity, a real snapshot restore, and a live LLM using delegation
and local Hermes config commands. An independent random file oracle must match;
an agent saying it succeeded is not sufficient. No LLM API is mocked.

## Run on a disposable Linux VM

Check out the candidate and fetch the OLD fork commit. Configure a dedicated,
spend-capped OpenAI-compatible gateway key for the run:

- Actions secret: `E2E_LLM_API_KEY`.
- Actions variables: `E2E_LLM_BASE_URL`, `E2E_LLM_MODEL`.
- Dispatch with the candidate commit and an OLD fork baseline. They must differ.

Locally, the same names prefixed `E2E_LLM_` configure the runner. Its process removes
those variables before starting installers/build workers and passes credentials to
the encrypted state writer over stdin. A credential JSON may instead arrive using
`--credentials-stdin`; never put credentials or a passphrase in command arguments.

```sh
python3 scripts/e2e/encrypted_lifecycle.py \
  --scenario update --baseline OLD_SHA --candidate NEW_SHA \
  --work-dir /path/to/disposable-work --evidence /path/to/evidence
```

The driver makes its own isolated HOME, HERMES_HOME and HERMES_RUNTIME_DIR. The real
installer/updater fetches from a local, exact-SHA Git transport; no fake release is
published and no production home is reused. The passphrase is generated in driver
memory and entered only through a PTY. One update prompt is required; extra prompts
fail immediately. Local commands inside the live session must not prompt again.

Update/broken-file baselines require their old runtime's system prerequisites
(Ubuntu: `libatomic1`). The workflow prepares those explicitly; the cold fresh
case must provision itself. For repeated disposable-VM debugging only,
`--tool-cache /path/to/pm-tools` can reuse hash-verified PM tools on update/dirty
legs, never fresh acceptance. Evidence records that reuse. No config, credentials
or session state belongs in that cache. Use an empty evidence directory per attempt.

## Evidence and interpretation

Each leg writes sanitized logs plus `receipt.json`: exact baseline/candidate,
harness digest, provider/model, prompt counts, semantic state hashes and restore
verdict. Failed, missing, cancelled or skipped legs do not pass acceptance. Missing
credentials fail before installation and never become evidence of live coverage.
Upload **only** the evidence directory, not work homes, OAuth stores, SSH keys or
runtime caches. The driver cleans work by default; `--keep-work` is for disposable
VM debugging only.

The ordinary suite checks the PTY driver through `scripts/run_tests.sh
tests/scripts/e2e/`; it deliberately receives no live credentials. Real-VM/live-LLM
receipts and isolated regression results are separate claims.
