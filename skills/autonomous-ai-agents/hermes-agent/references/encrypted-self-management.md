# Encrypted Self-Management

## Session unlock

The human unlocks Hermes once at session startup. The session retains an unlocked
key in memory, not the passphrase. Local `terminal` calls, background commands,
`delegate_task` agents and nested Hermes CLIs use temporary session authority.
Do not request a passphrase in chat, put one in a command/environment variable, or
copy vault keys. Do not print, save, or manually forward session credentials.

The grant belongs to the active home and session. Switching profiles requires
that profile's own session authority. Remote SSH/container backends do not receive
local vault keys. After the owner closes or locks, open an authorized session again;
do not bypass encryption or create a replacement home.

## Config and files

Use the native `terminal` tool to run the existing CLI:

```sh
hermes config get agent.max_turns
hermes config set agent.max_turns 90
hermes config set delegation.max_concurrent_children 4
hermes config check
```

Settings go through encrypted, atomic config writers; secrets belong in the encrypted
credential store, not `config.yaml`, argv, or chat. Use the local command's
supported stdin or interactive secret-input flow. Use native memory/skill tools for
their stores. Source files in the checkout are ordinary code and can be edited using
`read_file` and `patch`; runtime config, auth, databases and transcripts are not.

Config/skills/tool changes take effect in the next session unless the command offers
an explicit `--now` and the user requests it. Do not rebuild the active system prompt
or tool schema mid-conversation; preserve prompt caching.
MCP config watching only notifies by default. `/reload-mcp` explicitly applies the
change; `mcp.auto_reload_on_config_change: true` opts into cache-invalidating reloads.

## Update and recover

```sh
hermes update --help
hermes update
hermes secure-vault status
hermes config check
hermes doctor
```

Inside an unlocked local session the updater uses that authority. A standalone
interactive update asks once and hands the unlocked key to its own restart/verification
children; it never asks the model to supply a passphrase. Code and dependency updates
must preserve encrypted settings, credentials, history, memory, skills and scheduled
jobs. Do not reset or replace state to make an update pass.

If an update stops, retain its diagnostic/receipt and the existing home. Use
`hermes pm repair` for damaged dependencies and `hermes secure-vault repair --help`
for vault recovery. Repair never grants permission to discard unreadable secrets.
Inspect the reported failure before retrying. A revoked grant means the owning
session ended; it is not a reason to write plaintext or disable the vault.

For damaged dependencies use `hermes pm repair`; for supported plaintext-writer
or wrapped-database damage use `hermes secure-vault repair`. A nonzero repair
result means data still needs recovery, not permission to reset it. Saved
snapshots and other profiles must remain untouched.
Rerunning the source installer can recreate missing config/secrets files as
encrypted defaults, but cannot recover their deleted contents. Restore those
values from a retained backup; never overwrite an existing unreadable file.

For an explicitly requested bulk recovery/migration, use
`hermes secure-vault export --output /path/outside-hermes/recovery.tar` through
the native `terminal` tool. It writes a verified plaintext copy, including
secrets, outside the encrypted home; it never decrypts the original in place.
Keep that archive private, exclude it from logs/artifacts, and re-encrypt a
recovered home before normal use. Do not export as a workaround for ordinary
config writes or a missing session grant.

## Subagents

Use `delegate_task` normally; do not add key/passphrase parameters. Children share
the authorized home's unlock while maintaining their own conversations and tool
state. A child can run the same config commands and create encrypted state without
another prompt. Closing a child must not revoke the parent's access. New independent
sessions started after the owner closes must unlock normally.

## Unexpected unlock prompts

If a local command or subagent requests another passphrase, stop that operation
and check the active installation and home. Run `hermes --version` and
`hermes secure-vault status` through the native `terminal` tool, and inspect the
launcher's source checkout with `read_file`. An older or upstream installation
does not gain this fork's session authority just because the development checkout
was updated. Do not work around the mismatch by exporting credentials, changing
the home, or replacing the launcher by hand.

Update the intended encrypted-fork installation through its normal update flow,
then open a new session so it loads the new runtime and bundled guides. Updating
files does not replace code or cached instructions in an already-running session.
If a bundled guide was locally edited, preserve those edits: inspect
`hermes skills diff hermes-agent` before considering
`hermes skills reset hermes-agent --restore`. Restoring the bundled copy replaces
the local copy; do not do that automatically.
