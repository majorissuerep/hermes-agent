---
sidebar_position: 9
title: "Legacy Nous accounts"
description: "Existing account and managed-tool compatibility in the encrypted fork"
---

# Legacy Nous accounts

Nous Portal is **not a bundled inference provider in this fork**. New installations
choose from [AI Providers](./providers.md); there is no anonymous welcome-provider
fallback. This page covers retained account records and optional managed tools,
not a recommended model-setup path.

## Existing account data

Updates preserve existing credentials and account records. A retired inference
provider does not become a supported route merely because its token is present.
Select an explicit supported provider with `hermes model`, or install a provider
plugin deliberately if you need that legacy integration.

Inspect the retained account without selecting a model:

```sh
hermes portal info
hermes portal --help
```

`hermes portal` defaults to account information in this fork. It does not silently
sign in, claim a welcome offer, or replace the selected inference provider.
Legacy inference/authentication instructions from upstream require an appropriate
provider plugin and are not the default product flow here.

## Mixing the gateway with your own backends

Managed web, image, speech, or browser services are separate from the inference
provider. An existing account may still authorize those optional services. Use
`hermes tools` to select each backend explicitly; keep your model provider selected
independently with `hermes model`.

A missing credential or exhausted account is not consent to switch to a different
paid backend. Inspect the selected backend and account status before changing it.
For service-specific details, see [Tool Gateway](../user-guide/features/tool-gateway.md).

## Token handling

Logical credential records live in the active Hermes home's authentication and
secret stores. In this fork those stores, configuration, transcripts, and databases
are encrypted at rest. Do not edit `auth.json`, `.env`, or `config.yaml` as plaintext,
print tokens, or copy a session grant to another profile.

Unlock once when starting an authorized session. Local Hermes CLI commands and
subagents reuse temporary session authority, not the passphrase. A standalone update
asks once and carries its unlock through verification and restart children.

## Profile setup

Profiles are independent credential islands. Select the intended profile before
configuring a connection:

```sh
hermes -p work model
hermes -p work portal info
```

A profile without its own usable credential fails closed. The default profile's
credentials are not a fallback. Existing explicit shared-account arrangements do
not imply live configuration inheritance between profiles.

## Supported platforms

This fork supports Linux and macOS; Windows users use WSL rather than a native
Windows installation. See [Platform Support](../getting-started/platform-support.md).

For the original upstream product's Portal integration, consult its
[upstream documentation](https://hermes-agent.nousresearch.com/docs/integrations/nous-portal).
Those instructions describe upstream capabilities, not this fork's bundled provider menu.
