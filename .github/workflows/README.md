# Workflow policy

`quality.yml` is the sole GitHub entrypoint for the public mirror. Historical
workflows remain disabled qualification references and fixtures. Do not enable
the old fan-out alongside the in-house verifier.

Routine validation has one command, `python3 scripts/ci/verify.py`, in
`.woodpecker/verify.yml`. It reuses the classifier, pinned PM toolchain, blocking
guards and canonical test runner. GitHub checks the exact revision's latest
trusted Woodpecker result, then supplies native Linux/macOS, real SSH/Docker and
installer protocol evidence through two host jobs and one aggregate gate.

`.woodpecker/mirror.yml` runs only on source-main pushes after verification. It
pushes `mirror/qualification`, waits for GitHub Actions' unchanged required
`All required checks pass (v2)`, then fast-forwards public main and verifies
readback. Failed, pending, cancelled or missing checks cannot qualify publication.
The deploy key belongs only to this repository; source access is read-only.
Neither credential is available to pull-request steps. No workflow publishes a
deployment or changes repository visibility/protection.

Live-provider and cold lifecycle acceptance remain separate, explicit runs.
See [the tooling contract](../../website/docs/developer-guide/secure-tooling.md).
