---
title: Encrypted tooling and upstream maintenance
---

# Encrypted tooling and upstream maintenance

The operator outcome is a local agent that can maintain its own installation and
use native, Codex and Claude children after one human unlock. Configuration,
credentials, history and owned outputs must remain encrypted. A successful
command exit alone does not establish that outcome.

## Baseline and boundaries

The starting fork revision is `1815472adf69eb16cc201c0d9ac4c7e69f5fa51b`.
On 2026-11-02 upstream main was fetched at
`5e7c2b254d414e4e25105231f818f385fee67469`: 6,826 upstream commits after
the shared base `eb75627faf532fa6ffbdae59a0d14b0b385fc780`. The unfinished
`sync/upstream-20261022` and `ci/gitea-primary-20261028` branches are inputs to
review, not accepted product evidence.

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

The consequential assumptions are that the public command inherits the correct
installation and grant, and that external executors preserve the grant through
their own shell boundaries. Real child commands against synthetic encrypted
homes falsify those assumptions. Loopback/provider fixtures establish protocol
behavior only. An actual native host/account run is required for a live claim.
Keep missing evidence pending. Retain failing receipts and preserve original
homes and revision identities for rollback; never decrypt a home in place to
make a test pass.

## Evidence and review

Record the final candidate revision, commands, native OS, protocol or live
provider, failures, skipped coverage and cleanup here as work is verified.
The fork maintainer owns review after an unlock regression, plaintext-state
finding, child ownership failure, upstream boundary change or CI coverage drift.
