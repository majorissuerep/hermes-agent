---
title: Experimental formal logic mode
---

# Experimental formal logic mode

Formal logic mode adds automatic Lean 4 checks to intermediate checkpoints and
final answers. It is disabled by default. The current language is finite
propositional logic: at most ten Boolean atoms, sixteen assumptions and sixteen
hypotheses per checkpoint. It does not formalize arbitrary mathematics or prove
that a statement about the world is true.

The model supplies a JSON checkpoint containing atom meanings, explicit
assumptions, deductions and a named conclusion. Hermes generates the Lean source
itself; model-supplied Lean programs, imports, tactics and axioms are not accepted.
The runtime rejects inconsistent assumptions and invalid deductions, then checks
the generated consistency and deduction theorems with Lean 4.35.0. Every obligation
must report no axioms. A deliberately false theorem must fail before a certificate
can be accepted. A successful process exit without the expected diagnostics is
insufficient.

## Enable for a new session

Install the pinned official compiler into a new directory:

```sh
python scripts/ci/install_lean.py --directory /absolute/path/to/new-lean-runtime
```

The installer prints the compiler path, verifies the platform-specific release
archive SHA-256 before extraction, and refuses to overwrite an existing directory.
It requires a native Linux or macOS host and tar with Zstandard support.

Set the owning profile's encrypted configuration through the normal command:

```sh
hermes config set agent.formal_logic.lean_path /absolute/path/to/new-lean-runtime/lean-4.35.0-linux/bin/lean
hermes config set agent.formal_logic.enabled true
```

Use the path printed by the installer on your actual platform. Start a new
conversation after enabling or disabling the mode:

```sh
hermes config set agent.formal_logic.enabled false
```

The settings `agent.formal_logic.enabled`, `agent.formal_logic.lean_path` and
`agent.formal_logic.max_repairs` are read once for a session. The last defaults
to three automatic repair attempts and accepts values from one to eight.
The mode uses the native Hermes conversation loop. A top-level
Codex app-server runtime is refused because its internal human requests cannot
be gated by this loop. Managed children follow the runner's
[native OS and protected-workspace requirements](encrypted-state.md#local-coding-agents);
their outputs still require checkpoints in the owning conversation.

Instructions enter the new user-turn context; cached system prompts, tool
schemas and earlier messages are preserved.

## Checkpoints and feedback

A checkpoint accompanies a concise answer explaining its conditional conclusion:

```hermes_logic
{
  "version": 1,
  "atoms": {"P": "P holds", "Q": "Q holds"},
  "assumptions": [
    {"id": "a", "statement": "P holds", "formula": "P", "kind": "given", "quote": "Assume P"},
    {"id": "b", "statement": "P implies Q", "formula": ["implies", "P", "Q"], "kind": "given", "quote": "P implies Q"}
  ],
  "hypotheses": [
    {"id": "h", "statement": "Q holds", "formula": "Q", "from": ["a", "b"]}
  ],
  "conclusion": "h"
}
```

Formulas are atom names, JSON booleans, or arrays using `not`, `and`, `or`,
`implies` and `iff`. A hypothesis may depend only on named assumptions and
earlier proved hypotheses. Premises need explicit provenance:

- `given` supplies an exact `quote` that explicitly stipulates the assumption
  in the actual user task.
- `fact` supplies a public `query`. Once logic passes, Hermes automatically
  searches and extracts source pages with the owning profile's configured web
  providers. The query is sent to those providers; private preferences belong
  in stakeholder assumptions. Up to four factual assumptions are reviewed per
  checkpoint.
- `stakeholder` supplies a `question` about a real user-owned preference,
  private fact or decision.

Automatic review uses `auxiliary.formal_logic` through the existing
profile-aware auxiliary routing. It checks the relationship between the informal
answer and formal statement, explicit givens, source support and whether a
stakeholder question is necessary. Retrieved pages are untrusted evidence.
Factual citations need exact supporting passages present in successfully
extracted pages; invented URLs or quotations are refused.
This review is a model assessment of evidence, separate from the kernel proof.
The current task and proposed answer are each limited to 16,000 characters.
Oversized inputs fail automatic review; their endings are never silently omitted.

Failed logic and unavailable or unhealthy checkers return repair feedback
automatically. They never qualify stakeholder questions. Unsupported public
facts likewise require automatic revision rather than asking a person to fix
the search. If logic and automatic review pass, a genuinely necessary
stakeholder assumption can use the exact pending `clarify` question with
`Accept assumption` and `Reject assumption` choices. A skipped or rejected
answer does not grant acceptance.

A failed repair budget or exhausted conversation budget returns an explicit
unverified outcome. The native result's `formal_logic` receipt records the
compiler version, checkpoint/source digests, obligation count, assumption review
and acceptance flag. Conversation checkpoints, feedback and decisions use the
existing encrypted transcript; generated Lean source is supplied through stdin. Compiler version probes,
canary checks and proofs share an empty working directory and receive no owner
credentials or session grants.

## Research and qualification

The design follows the symbolic formulation, deterministic solver and feedback
cycle in [Logic-LM](https://arxiv.org/abs/2305.12295). It also draws on the
interactive theorem-proving feedback pattern in
[Lean Copilot](https://github.com/lean-dojo/LeanCopilot).
Lean's [axiom documentation](https://lean-lang.org/doc/reference/latest/Axioms)
explains why a compiled declaration or a `sorry` warning alone cannot establish
an accepted proof.

The ordinary suite tests the language, sequencing, review provenance, session
toggle and bounded failure behavior. Native host qualification additionally
installs the pinned compiler and runs:

```sh
scripts/run_tests.sh --file-retries 0 --fail-on-skip tests/e2e/core/formal_logic/test_native_checkpoints.py
```

These contracts drive the real encrypted CLI against a recording model endpoint,
exercise the actual Lean compiler, observe repair feedback on the wire, and
check encrypted persistence. Native qualification also drives the public Ink
keyboard path through a failed proof, automatic repair, verified delivery and
disabling the mode for a new owner. A fixture model qualifies orchestration and
protocol behavior; it does not establish the reasoning quality of a live model.
