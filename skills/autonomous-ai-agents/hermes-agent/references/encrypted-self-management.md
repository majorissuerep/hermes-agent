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
credential store, not `config.yaml`, argv, or chat. For MCP secret input use the
existing `set-api-key --value-stdin` host operation. Use native memory/skill tools for
their stores. Source files in the checkout are ordinary code and can be edited using
`read_file` and `patch`; runtime config, auth, databases and transcripts are not.

Config/skills/tool changes take effect in the next session unless the command offers
an explicit `--now` and the user requests it. Do not rebuild the active system prompt
or tool schema mid-conversation; preserve prompt caching.

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

## Subagents

Use `delegate_task` normally; do not add key/passphrase parameters. Children share
the authorized home's unlock while maintaining their own conversations and tool
state. A child can run the same config commands and create encrypted state without
another prompt. Closing a child must not revoke the parent's access. New independent
sessions started after the owner closes must unlock normally.
