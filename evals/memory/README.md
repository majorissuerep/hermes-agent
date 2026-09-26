# Encrypted-memory qualification

These are storage/lifecycle contracts and a paired cost measurement, not evidence of
better semantic retrieval or model task quality. Use synthetic temporary homes only.
No model API, embedding download, live configuration or credentials are required.

## Process contracts

Run the existing provider battery plus the lifecycle/long-lived-reader tests through
the canonical isolated runner:

```bash
scripts/run_tests.sh tests/plugins/memory/test_lancedb*.py \
  tests/hermes_security/test_frame_write_integrity.py -j 2 --file-retries 0
```

The long-lived-reader test starts a separate spawned process, then checks the production
reader after another process writes, erases and atomically rewrites the log. The other
lifecycle test starts fresh Python processes to write/read persisted IDs. The damaged-log
contract permits explicit rejection before acknowledgement; it does not promise automatic
repair or claim that all synthetic truncation patterns can be distinguished from corruption.
The filesystem fault tests are controlled faults, not observations of lost live data.

## Paired append-cost measurement

Use the SAME interpreter, script, machine and settings, with immutable base/candidate
worktrees. The script records exact Git SHA and dirty status; keep raw JSON outside both
worktrees. Reject dirty receipts for release comparisons.

```bash
python evals/memory/frame_append_benchmark.py /absolute/base/tree > /tmp/frame-base.json
python evals/memory/frame_append_benchmark.py /absolute/candidate/tree > /tmp/frame-candidate.json
```

Three independent temporary vaults per log size; 257-byte payloads; one cold append and
20 warm appends per trial; counts of 100, 1,000 and 10,000 existing records. All acknowledged
payloads must survive reopening. Raw samples are included, not only averages. Invalid tree
or repetition arguments fail nonzero. Temporary roots are removed after each trial.

This measures encrypted frame append cost only: not end-to-end memory-put latency,
retrieval ranking, OS power-loss durability, model quality, or performance at larger sizes.
The strict writer validates changed streams and hashes the existing bytes under the lock
on warm appends. That cost grows linearly with log size; it is not a speedup. Measure cold-start
and multiwriter costs separately before drawing scale conclusions.

## Task-quality boundary

The existing `evals/core_tool_deferral/{orchestrator,worker,tasks}.py` machinery has actual
model tasks and graders. Do not present its current OpenRouter-only, plaintext-home,
`skip_memory=True` worker or stubbed GUI tools as qualification of encrypted memory.
Those assumptions must be adapted and validated before an encrypted-memory/model-quality
comparison. No such quality gain is claimed by this change. A CLI preflight answering
READY is authentication evidence, not task success.
