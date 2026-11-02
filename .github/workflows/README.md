# Workflow policy

Repository Actions is currently disabled. The historical reusable workflows in
this directory remain qualification references and fixtures. Do not re-enable
the old orchestration to obtain a missing required status.

Routine validation has one command, `python3 scripts/ci/verify.py`, and one
Woodpecker definition, `.woodpecker/verify.yml`. It reuses the existing affected
area classifier, pinned PM toolchain, blocking guards and canonical test runner.
It does not publish source, packages, images or deployments.

The existing GitHub required status is unchanged. A local pass or a Woodpecker
pass does not satisfy a required status from a different application. Missing
required checks block a merge until the repository's established CI supplies
them. See [the tooling contract](../../../website/docs/developer-guide/secure-tooling.md).
