---
title: Encrypted state and recovery
description: "Unlock once, manage Hermes normally, and recover without replacing user state."
---

# Encrypted state and recovery

This fork encrypts configuration, credentials, databases, memory, transcripts,
and logs at rest. Source code and installed skill/plugin code remain ordinary
files. Keep your passphrase or private key: encryption has no password-reset
backdoor.

## Everyday self-management

Unlock once when starting a session. Local Hermes commands and delegated agents
reuse temporary, home-scoped authority; they do not need the passphrase again.
The passphrase is not stored in session history, environment variables, or argv.
Closing the owning session revokes its authority. Another profile or independent
session still needs its own unlock.

```sh
hermes config get agent.max_turns
hermes config set agent.max_turns 90
hermes secure-vault status
hermes update --backup
```

A standalone update asks once and carries the unlock through its own handoff and
verification. Config/tool changes normally take effect next session; do not
reload the system prompt in a running conversation merely to apply a file edit.
Never edit encrypted config, auth, or database bytes as plaintext.

## Diagnose and repair

```sh
hermes doctor
hermes pm repair
hermes secure-vault repair
```

PM repairs managed dependencies. Vault repair handles old plaintext config/env
writers, wrapped databases, and mixed encrypted/plaintext log streams. It leaves
saved snapshots and other profiles alone. A plaintext database or unrecoverable
file causes a nonzero result; it is not silently reset. Retain the damaged file
and restore a known-good snapshot through `/snapshot` in the CLI.

If source or the interpreter is missing, rerun this fork's source installer
against the **same** installation/home. The installer retains local work in Git
recovery refs/stashes. Do not delete the state home to make installation succeed.
Missing config/secrets files receive encrypted, empty defaults after the unlock;
existing files are never replaced. Defaults cannot reconstruct deleted values:
restore their original settings and credentials from a retained backup.

## Restore a retained backup

Quick snapshots and full ZIP backups created before enabling encryption remain
restorable in that same home. A legacy ZIP stored inside the home may itself be
encrypted; Hermes reads it through the unlocked vault without rewriting it.

```sh
hermes import /path/to/retained.zip --force
```

Plaintext legacy settings, secrets and databases are authenticated/converted in
private staging before publication; restored state remains encrypted. SQLCipher
database contents and header metadata are verified, and complete log streams are
retained. Existing encrypted backups for the same vault work too. An import that
cannot restore a file returns failure instead of reporting complete recovery.

Import refuses different vault metadata before replacing any files. Do not overlay
another vault on a live home. For cross-vault migration, use the verified plaintext
export below and re-encrypt a separate copy with its own credential.

## Bulk decryption for recovery or migration

Close the profile's local terminal sessions before making an offline recovery
copy. Choose a **new** output outside every encrypted Hermes home:

```sh
hermes secure-vault export --output /path/outside-hermes/recovery.tar
```

This explicitly creates a **plaintext** archive, including secrets, with
owner-only permissions. Keep it private. It authenticates encrypted files,
exports SQLCipher databases including committed WAL data, verifies database
contents and metadata, and decodes complete log/transcript streams. Publication
is atomic; an existing output is never overwritten, and failures remove staging
files. The encrypted original is unchanged.

The archive includes a SHA-256 inventory (`.hermes-export.json`). Like full
backups, it omits regenerable code/caches, prior backups, symlinks, runtime
PID/lock records, and master unlock material/metadata. User skills and the browser
credential vault (including its own key) are retained. Export each
profile separately. A truncated stream must be repaired first, not silently
discarded during export.

Extract into a new directory to inspect or migrate the copy. To use it as a
Hermes home again, select that isolated home and run `hermes secure-vault migrate`
to re-encrypt it before normal use. Validate the copy before removing any recovery
material. See the [qualification contract](../developer-guide/reliability.md).
