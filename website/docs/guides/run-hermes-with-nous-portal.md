---
sidebar_position: 9
title: "Existing Nous Portal installations"
description: "Keep account data and select an explicit supported inference connection"
---

# Existing Nous Portal installations

This fork no longer bundles Nous as an inference provider or recommends Portal
first-run setup. Use [Quick Start](../getting-started/quickstart.md) and choose one
of the [supported connections](../integrations/providers.md).

Existing credentials and managed-tool settings are preserved during update. They
are not converted into an anonymous account or silently rerouted to another model
vendor. Select the active profile and inspect it:

```sh
hermes -p work portal info
hermes -p work model
hermes -p work tools
```

Model inference and optional managed tools are separate choices. A legacy inference
integration requires an explicitly installed provider plugin; it is not restored
by copying tokens or editing encrypted files.

See [Legacy Nous accounts](../integrations/nous-portal.md) for account handling,
[profile scope](../integrations/nous-portal.md#profile-setup), and encrypted-state rules.
