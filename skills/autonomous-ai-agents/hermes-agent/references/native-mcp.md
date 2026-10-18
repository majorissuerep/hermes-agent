# Outgoing MCP tool connections

Hermes is an MCP **client**: it connects to local stdio or remote HTTP MCP servers
and exposes selected tools to its agent. It does not serve Hermes state through an
inbound management/MCP server.

## Configure and verify

Inside an unlocked Hermes session, use the normal `terminal` tool:

```sh
hermes mcp list
hermes mcp add time --command uvx --args mcp-server-time
hermes mcp test time
hermes mcp remove time
```

Place `--args` last. Use `hermes mcp add --help` for supported authentication and
transport options, `hermes mcp catalog` for curated choices, and `hermes mcp configure
NAME` for tool selection. `hermes pm install --extra mcp` adds the pinned SDK if absent.
Do not install unbounded dependencies into a running Hermes environment.

Saving an entry is not proof of a working connection: inspect the probe and its exit
status. Local subprocesses use the current profile's encrypted settings and temporary
session authority without another passphrase; they do not call a management host.
Removed `--via-host`, `set-api-key` and `mcp serve` routes must not be suggested.

## Secrets and activation

Keep secret values in encrypted credentials. Existing MCP entry `env` / header
references such as `${SERVICE_TOKEN}` resolve from the owning profile. Do not expose
a secret in chat, argv, logs or a plaintext config rewrite. Use the interactive
credential/setup flow; never ask the user to paste the vault passphrase into chat.
See `encrypted-self-management.md` for the key-authority boundary.

Changed connections and tool selection normally activate next session. Do not
reload the tool schema mid-conversation on your own: it breaks the cached prompt
prefix. For explicit immediate activation, use only the installed `/help` command's
supported reload form after discussing that consequence.

## Diagnosis

1. Run `hermes mcp list` and check the intended profile.
2. Run `hermes mcp test NAME`; distinguish authentication, transport and discovery failures.
3. Check command availability and the target server's actual requirements.
4. Use `hermes logs` and the profile's `logs/mcp-stderr.log`, redacting credentials.
5. Start a new session after a successful configuration change.

An outgoing HTTP server has its own credentials and trust boundary. Local session
unlock authority must never be forwarded to it or to SSH/container targets.
