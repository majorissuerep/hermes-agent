<!-- First line: the user-visible effect. Link a real issue if one exists. -->

## What and why

<!-- Explain the problem, final behavior and scope in 2–3 sentences. Search open
issues/PRs for overlap; link related work or state that none was found. A direct
user request needs no invented issue or extra approval. -->

## Evidence

- Revision and environment:
- Bug reproduction / failing before, passing after (or why not applicable):
- Checks: <!-- Exact commands, exit status and final output. Use
  scripts/run_tests.sh <affected paths> --file-retries 0, never bare pytest;
  python scripts/check; and the applicable area's build/type/security checks.
  List unavailable checks as unverified. -->
- Performance only: <!-- Same command/environment, baseline and after. -->
- Edge cases: <!-- Relevant empty/null, error, boundary, concurrency and auth
  cases, with covering tests or a reason they do not apply. -->

## Risk and rollback

<!-- What could break, failure behavior, existing logs/monitoring, rollout or
existing feature flag where appropriate, and how to revert. -->

## Review

<!-- Self-review or fresh-context review: findings and how each was handled.
Reply to actionable review comments with a fixing commit or reason. Leave
resolution to the reviewer unless explicitly asked. -->

- [ ] One concern; no unrelated dependencies, refactors or formatting. Above
  5 files or 300 changed lines, explain why it stays together or split it.
- [ ] Followed [contribution rules](../CONTRIBUTING.md) and area instructions.
- [ ] Required checks passed on the current candidate; no weakened checks.
- [ ] Relevant docs/config/schema examples updated, or not applicable.
- [ ] Security reports follow [SECURITY.md](../SECURITY.md); no secrets in artifacts.

<!-- Installed prompts, passing tests and merged PRs establish different claims.
Do not claim outcome improvement without replay/live/post-merge evidence. -->
