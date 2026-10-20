---
sidebar_label: JSON management
---

# Local JSON management

`hermes manage` is the machine interface for encrypted configuration, secret and
passphrase changes on Linux and macOS. It is a local CLI, not an inbound server
or an additional model tool. Discover its versioned contract without unlocking:

```sh
hermes manage schema
hermes manage --help
```

Send **one JSON object on stdin** to `hermes manage apply`. Every application
response is one JSON object: `{ "version": 1, "ok": true, "result": ... }`;
refusals return `ok: false`, `error.code` / `error.message`, and exit status 2.
An unlocked owning session supplies its existing scoped authority. This command
never requests a password in chat or silently creates an unencrypted home.
Use `hermes manage --profile NAME apply` for an existing profile; the grant must
authorize that exact profile. A wrong-profile grant is refused.

## Configuration

```json
{"op":"config.get","path":[]}
```

This returns raw, **redacted** user settings and a `revision`. Defaults are not
written back, environment references are not expanded, and credential/header
values plus URL user information/query values are hidden. Read the whole object
to discover custom settings; paths are arrays, so a provider named `my.route`
is one key, not two. Integer path segments address existing list elements.

Use the returned revision for a write:

```json
{"op":"config.set","revision":"<returned revision>","path":["agent","max_turns"],"value":90}
```

`config.unset` uses the same fields except `value`. A batch validates all changes
before one comment-preserving encrypted atomic commit:

```json
{"op":"config.batch","revision":"<returned revision>","operations":[
  {"op":"config.set","path":["providers","my.route","api_mode"],"value":"anthropic_messages"},
  {"op":"config.set","path":["providers","my.route","key_env"],"value":"MY_RELAY_KEY"},
  {"op":"config.unset","path":["custom","obsolete"]}
]}
```

The partial canonical schema validates known types and structures; custom/plugin
settings remain supported. A stale revision refuses the whole change. Config
writers share the same per-file cross-process lock, including legacy `config set`
and `config unset`. Batches are **config-only**; there is no atomic transaction
spanning config, credentials and vault metadata. Unrelated keys/comments survive.
Prompt-affecting changes take effect in the **next session**, not by rewriting an
active cached conversation. Changing provider alone clears proven stale route
metadata through the canonical provider-routing policy.

## Secrets and passphrases

```json
{"op":"secret.set","name":"MY_RELAY_KEY","secret_fd":3}
```

`secret_fd` is an inherited, one-shot readable descriptor numbered at least 3,
distinct from stdin and the unlock FD. Supply one nonempty UTF-8 value, without
NUL or line endings, then EOF (maximum 64 KiB, **five-second total read deadline**).
Prepare the secret before launching the command; an unfinished stream returns
`secret_timeout` without publishing it. The CLI closes its descriptor
on success or refusal and never returns the value. The caller creates the pipe
from a trusted secret source or a human's secure local password prompt—**never
put secret values in argv, environment variables, requests or model transcripts**.
Registered and custom environment-secret names use the canonical encrypted
writer and its execution/security-variable denylist. Config uses references such
as `${MY_RELAY_KEY}`, not literal embedded credentials.

```json
{"op":"secret.unset","name":"MY_RELAY_KEY"}
```

Passphrase rotation uses a separate new-secret descriptor and the already
unlocked **exact vault owner**:

```json
{"op":"passphrase.change","secret_fd":3}
```

No second unlock is needed. An ancestor machine vault cannot be rotated using
only a profile's grant. This changes future password unlocks without replacing
the master key or rewriting existing encrypted payloads. Do not reuse the
unlock descriptor for the new password.

Passphrase change is **not master-key revocation**. Existing backups retain the
credentials and password protection captured when they were created; rotate or
retire those copies deliberately if a former password was compromised.

## Inspection and other lifecycle operations

`{"op":"status"}` reports the selected home, unlocked status and secret names,
never secret values. Missing, damaged, locked or unsafe symbolic state paths
fail closed; no empty replacement is used as recovery.

Use existing canonical commands for other lifecycle work:
`hermes profile`, `sessions`, `skills`, `mcp`, `plugins`, `cron`, `backup`, `import`,
`secure-vault`, and `update` (each has `--help`). `manage schema` catalogues these
commands; it does not pretend to implement them as generic JSON transactions.
