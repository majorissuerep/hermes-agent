# Workflow policy

`quality.yml` is the sole GitHub entrypoint for the public mirror. Historical
workflows remain disabled qualification references and fixtures. Do not enable
the old fan-out alongside the in-house verifier.

Routine validation has one command, `python3 scripts/ci/verify.py`, in
`.woodpecker/verify.yml`. It reuses the classifier, pinned PM toolchain, blocking
guards and canonical test runner. GitHub supplies native Linux/macOS, real
SSH/Docker and installer protocol evidence through two host jobs and one
aggregate gate. Hosted runners do not need access to the private forge.

`.woodpecker/mirror.yml` runs only on source-main pushes after verification. It
requires the exact revision's latest trusted Woodpecker result before pushing
`mirror/qualification`, waits for GitHub Actions' unchanged required
`All required checks pass (v2)`, then fast-forwards public main and verifies
readback. It rechecks private verification immediately before the main push.
Failed, pending, cancelled or missing checks cannot qualify publication.
The deploy key belongs only to this repository; source access is read-only.
The publishing bindings are push-only, and PR steps cannot receive the mirror
write key. No workflow publishes a deployment or changes repository
visibility/protection.

The clone image receives a repository-only read-only SSH key on PR, manual and
push events through its documented plugin settings, with a pinned host key.
It cannot use the mirror write key. This binding avoids an hourly OAuth
snapshot expiring between Git fetches during a slow checkout; the shared CI
bot's interactive login remains unchanged.
The clone uses Gitea's private cluster service. Operators apply
`scripts/ci/woodpecker-clone-egress.yaml` to allow only repository 17's `git`
pods to reach the Gitea SSH pod port; verification pods keep their existing
default-deny policy. This adds no public endpoint.

Live-provider and cold lifecycle acceptance remain separate, explicit runs.
See [the tooling contract](../../website/docs/developer-guide/secure-tooling.md).

A manual dispatch of the same workflow may select complete terminal payload
builds. Supply its exact source SHA and dispatch that same ref. Both native
qualification jobs still run, and the aggregate requires their success plus
every selected payload build. Ordinary qualification does not build assets.
The uploaded archives contain the compiled Ink product and a source/hash
receipt; they are build outputs awaiting independent native artifact lifecycle
acceptance. This workflow creates no release or tag.
