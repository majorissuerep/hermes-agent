---
title: Encrypted state and recovery
description: "Unlock once, manage Hermes normally, and recover without replacing user state."
---

# Encrypted state and recovery

This fork encrypts configuration, credentials, databases, memory, transcripts,
and logs at rest. Source code and installed skill/plugin code remain ordinary
files. Keep your passphrase or private key: encryption has no password-reset
backdoor.

Encrypted databases keep SQLite temporary tables, sorting work and other scratch
storage in memory. A legacy `database.temp_store` setting cannot select file
storage: Hermes reports the effective `MEMORY` setting and preserves the saved
value so you can remove the obsolete option yourself. Ordinary external SQLite
databases retain their configuration behavior. Memory storage can still be paged
to swap by the operating system; it is not a guarantee that plaintext never
reaches swap.

## Everyday self-management

Unlock once when starting a session. Local Hermes commands and delegated agents
reuse temporary, home-scoped authority; they do not need the passphrase again.
The passphrase is not stored in session history, environment variables, or argv.
Closing the owning session revokes its authority, including retained borrowed
vault handles. An explicit lock or cache clear wipes retained key custody; a new
unlock creates a new owner and cannot revive an old handle. Another profile or
independent session still needs its own unlock.

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

### Local coding agents

From an unlocked session, use the installed CLI through its managed runner:

```sh
hermes manage schema
hermes manage run hermes config get agent.max_turns
hermes manage run codex -- --sandbox workspace-write "Inspect this project"
hermes manage run claude -- --allowedTools Read,Grep,Glob "Inspect this project"
```

Install and authenticate Codex or Claude separately. These foreground runners
lend only the selected profile's temporary operation authority and its own
vendor API key when configured. A general child grant cannot export a vault key;
trusted native Hermes children receive a separate one-shot kernel descriptor. They disable the executor's additional conversation
persistence and keep its permission controls. A child can call `hermes config`
or `hermes manage apply` without asking for the passphrase again. JSON management
uses revision-checked encrypted writes; see `hermes manage schema` for operations.

Protected Codex/Claude scratch requires an existing directory proven to be RAM
that cannot be swapped. Select it with the owning profile's
`security.protected_work_dir` setting; otherwise Hermes checks `XDG_RUNTIME_DIR`.
The current proof backend is Linux: ramfs, tmpfs mounted with `noswap`, or tmpfs
on a host with no active swap. An ordinary tmpfs with active or unknown swap
does not qualify. A missing proof returns an unavailable outcome before starting
the vendor. This protected vendor path is currently unavailable on macOS;
native Hermes self-management remains available.

On Linux, the supervisor stops the managed process tree on owner death or
cancellation and removes the exact owned scratch directory after the tree ends.
It retains parent-directory descriptors and inode identities, so a replacement
directory or symlink cannot redirect that cleanup. The vendor inherits neither
those descriptors nor a vault key.

Codex's workspace sandbox still requires explicit `--add-dir /path/to/profile`
authorization for direct file writes outside the workspace. Mediated management
writes execute in the owning Hermes process. Only the private kernel-pipe
transport directory is added automatically. No vault key or passphrase is put
in that directory, argv or environment. Do not print temporary grants, disable
the executor's confinement to solve a refusal, or resume a managed task outside
its owner session. Closing the owner revokes subsequent child redemptions.

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

Managed named profiles beneath a machine vault keep the existing one-unlock
model. Their complete encrypted local settings authenticate the selected
profile; an independently initialized profile always requires its own usable
vault. New profile log/transcript streams also bind their encryption key to the
logical profile, so copying a sibling's encrypted stream does not grant access.

For an older machine-owned profile whose streams predate that binding, select
that profile and run `hermes secure-vault repair` offline. The exclusive repair
authenticates each complete old stream, preserves its exact original ciphertext
in an encrypted envelope under `backups/profile-frames`, then binds the new
stream to the selected profile. This explicit adoption cannot establish the
historical profile of an old stream that shared the machine-wide key. Runtime
readers never try that legacy key as a fallback. Damaged or incomplete streams
remain unchanged and return a retained/nonzero result.

If source or the interpreter is missing, rerun this fork's source installer
against the **same** installation/home. The installer retains local work in Git
recovery refs/stashes. Do not delete the state home to make installation succeed.
Missing config/secrets files receive encrypted, empty defaults after the unlock;
existing files are never replaced. Defaults cannot reconstruct deleted values:
restore their original settings and credentials from a retained backup.

### Private native browser state

Native agent-browser uses an owned RAM workspace for its HOME, temporary Chromium
profiles, caches, logs and private IPC. Its configuration, auth and explicitly saved
session state are checkpointed into encrypted `browser-worker/` archives. The default
Chromium profile remains ephemeral; it stays in RAM until that browser exits.
This path requires the Linux RAM proof described above. An unavailable proof, locked
vault or damaged archive refuses the browser instead of using persistent scratch or
an empty replacement. The presence of a `CI` marker alone never authorizes disabling
Chromium's sandbox; explicit browser flags retain their existing meaning.

An existing `.agent-browser` directory is preserved and requires a deliberate import.
Close its browsers and Hermes sessions, select the owning profile, and run:

```sh
hermes secure-vault repair --native-browser-state /path/to/original/.agent-browser
```

Repair keeps an encrypted snapshot of that original directory, restores and compares
the complete state in RAM, and publishes its new encrypted archive before retiring
the old directory. It refuses live or foreign sources and existing destination state
that would require a merge. Deleting the old directory does not securely erase old
SSD blocks. Retain the reported recovery archive and its original vault credential.

Every queued browser operation retains its original session authority. A later unlock
or live sibling cannot revive it; a fresh sibling operation can use shared profile
configuration. A failed checkpoint retains both the latest RAM state and the previous
archive. For a fresh named native Chrome daemon, Hermes preserves the exact selected
saved-state bytes in RAM before bootstrap and explicitly loads that input once. It
does not reload an already-active browser. Ambiguous historical snapshot ordering
refuses automatic selection and retains every file for explicit recovery.

A failed bootstrap retains its exact input and last verified archive. After closing
the browser, a logged-in owner can inspect/recover authenticated worker state with:

```sh
printf '%s\n' '{"op":"browser.worker.repair"}' | hermes manage apply
```

An unverified bootstrap is reported as retained, and the original command can then
retry the same verified input; it is never reported as a saved empty replacement.
Ordinary repair does not import arbitrary external paths. After closing all owners,
`hermes secure-vault repair` saves and retires
quiescent worker RAM state. A live or unknown process identity prevents retirement.
If retirement is interrupted, repair finishes from the already verified checkpoint;
it never replaces that checkpoint with partially removed RAM files. Repair checks
the whole remainder against the full checkpoint and an encrypted hash-only inventory
of original control entries before deleting anything. New or changed data, unavailable
proof, or insufficient proven RAM retain the remaining copies. Older running sessions
refuse the closed cleanup record; finish it with the current version. Proofless
development retirement records can be read as passive history, never cleanup authority. A replaced
directory, changed checkpoint or revoked caller is retained for inspection instead
of being deleted. Keep the reported locations and encrypted archive on refusal.
Encrypted pending references identify the original RAM location even if the configured
workspace changes. If RAM is truly missing, explicit repair authenticates the last
checkpoint and records the missing-copy history before allowing that checkpoint to be
used again. It requires known-dead owners or a verified changed machine incarnation,
reports that uncheckpointed changes are unverifiable, and keeps the original archive.
Full ZIP backups retain worker ciphertext; ordinary plaintext export excludes this
credential-bearing domain. RAM filesystem proof does not cover Python heap swap or
the confidentiality of a hypervisor's memory.

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


### Interrupted migration and original rollback recovery

Migration establishes your intended credential and an encrypted checkpoint together,
then streams and verifies a full encrypted rollback outside the home before converting
any file. Keep its reported `.hmb` file and the original credential. A later credential
change cannot revoke credentials already known for a historical backup.

If migration stops, run `hermes secure-vault migrate --yes` again with the original
passphrase or external key file. Hermes authenticates the exact pending checkpoint
and complete rollback before resuming. Already-converted databases are checked by
their schema and data fingerprint, not by comparing physical SQLite bytes. Unexpected
changes are retained and reported; an unrelated initialized vault cannot be resumed.

If the live home's metadata is lost, recover from the rollback's embedded ordinary
public vault metadata and your original credential without changing the live home:

```sh
hermes secure-vault export --migration-backup /recovery/original.hmb --output /offline/original.tar
```

This is an explicitly requested **plaintext offline export**. The output must be new
and outside every encrypted Hermes home. The full rollback authenticates before any
member is used or output is published. Metadata staging needs a proven RAM-only
`XDG_RUNTIME_DIR`; an unavailable RAM proof refuses recovery rather than placing
private recovery work on SSD. The original rollback stays encrypted and unchanged.
Restore the recovered original data only through a deliberate offline recovery workflow.

Older `hermes-premigration-*.tar` files may already contain plaintext. Explicit vault
repair can still read them to recover damaged state; it never deletes their sole copy
or claims that replacing or deleting them erases historical SSD bytes.


### Named-profile boundaries during migration

Migration converts only the selected home. Its direct `profiles/` subtree is
retained as opaque data in the full encrypted rollback, including each child's
original credential metadata. It never unlocks, decrypts, rekeys or initializes
another profile. Unknown or markerless directories remain data; migration does
not turn them into new profiles. The snapshot requires the existing maintenance
fences to be available for those child homes.

The completion message applies to the **selected home**, not the whole machine.
A recognized named profile without usable own metadata is listed as requiring
its own migration or repair, and the CLI returns nonzero until that work is
selected separately. For example, choose the named profile explicitly with
`hermes --profile work secure-vault migrate`; an unattended migration must use
its own deliberately selected new external `--key-out` credential. Initialized
children are preserved opaquely, not authenticated with the selected home's key.

New named-profile rollbacks are stored outside the outermost validated vaulted
ancestor, with the same owner-private sibling fallback for immutable parent
directories. Old rollback locations remain passive recovery inputs; migration
never silently moves or deletes them.

Some historical profiles have encrypted files but no metadata of their own:
those files still use an ancestor vault and their original home-relative paths.
Independent migration refuses that state during its read-only plan, before
publishing a child credential or metadata that would shadow the old owner.
Keep the original ancestor metadata, original credential, existing ciphertext
and rollback. Recover a separate plaintext copy through deliberate offline
recovery with that original owning vault before migrating it independently.
Automatic cross-vault rekeying is unsupported; this refusal leaves the existing
legacy reader compatibility intact. Do not remove the ancestor metadata or
replace the only existing copy to force migration.
