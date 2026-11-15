# Coding quality and PR workflow

This workflow applies the operator-provided "AI Coding Agents & PR Quality:
Failure Modes, Prompt Fixes, and Verification" (2026-11-15). The coding posture
in `agent/coding_context.py` and verification nudge in `agent/verify_hooks.py`
carry the short runtime rules; this guide records the review and measurement
procedure. These are behavioral instructions, not an enforcement sandbox.

## Before and during implementation

1. Read applicable instructions and nearby implementations. Search open issues
   and PRs for overlap; coordinate or reuse existing work. Record inaccessible
   search sources. Explicit user direction authorizes work without an issue label.
2. State the behavior and expected files. Keep one concern; aim for 5 files or
   300 changed lines. Split independent work above that budget or explain why
   the authorized change needs a larger atomic diff, including required fixtures.
3. For bugs, establish a failing behavioral test before the fix where feasible;
   otherwise record a concrete reproduction and limitation. For performance,
   use the same command and environment for baseline and after measurements.
4. Use conventional names, explicit control flow and existing dependencies.
   Avoid unrelated rewrites, speculative abstractions and silent error fallbacks.
   Cover relevant null/empty, error, boundary, concurrency and authorization
   cases with tests or explain non-applicability. Use existing auth and secret
   storage patterns; never expose credentials in code, tests, logs or PRs.
5. After two failures with one approach or three red CI runs, stop repeating
   it, diagnose and report alternatives. Continue independently authorized work;
   seek direction only if no safe authorized alternative remains.

## Verification and review

Run `scripts/run_tests.sh <affected paths> --file-retries 0` and
`python scripts/check`, plus the area's required build, type and security checks.
For prompt changes, regenerate the production render with
`scripts/run-in-hermes-env python scripts/ci/prompt_surface.py render` and run
`scripts/run_tests.sh tests/ci/test_prompt_surface.py --file-retries 0`.
Record the revision, environment, commands, exit status, final output and limits.
Required local checks must pass before opening/updating a ready PR; current
required CI checks must also pass before merging. Never bypass failed, pending
or missing checks. Do not weaken checks to pass; an explicitly requested draft
can document known failures. Reverify every changed revision before merging.

Review the finished diff separately for logic, failure handling, security and
production behavior. If authorized and available, a fresh-context reviewer gets
only the task and diff; the author verifies findings. Otherwise identify the
review as a self-review. Use the PR template for effect, evidence, risk, monitoring
and rollback. In authorized review work, answer every actionable comment with a
fixing commit or reason; leave resolution to the reviewer unless asked.

## Measuring whether prompts help

Keep implementation/loading checks separate from outcome validation:

- Record model and prompt revisions, hashes, task type and cost. Preserve a
  baseline (about four weeks or 100 PRs) and change one rule group at a time.
- Replay 30-50 private historical tasks against old/new configurations, three
  runs each, using regression and held-out checks. Keep inputs and budgets fixed.
- Randomize live tasks between configurations; compare within task type. Track
  first-push CI pass rate, failed checks, diff size, duplicate closures, review
  rounds, unanswered comments, logic/security/error-handling findings and cost.
- Classify closures as agent failure, workflow or unknown; report unknowns
  separately. Include escalation counts and cost per merged PR.
- Wait for 30-day reverts, follow-up fixes and deployment incidents. Compare
  uncertainty intervals; a higher merge rate alone is not proof of correctness.
- Rebaseline after model/runtime changes. Do not automatically rewrite the
  evaluated rules or optimize them using merge rate alone.

## Local agent instructions

Use independently maintained instructions in Codex `~/.codex/AGENTS.md`, Claude
Code `~/.claude/CLAUDE.md`, and the owning Hermes profile's `SOUL.md`. Preserve
existing user preferences and back up originals. Verify each actual loader and
account for configured home overrides. Start a new session to adopt the changes;
do not rewrite cached conversation prefixes.

This fork's curator pin prevents automatic archival, not manual content edits;
it is not a prompt lock. Check installed capabilities before using a command
from an external guide. Repository revision and local file hashes identify this
policy; CI and existing branch protections remain the enforcement gates.
