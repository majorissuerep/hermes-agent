# Native MCP Operations

Hermes Agent has a built-in MCP client that connects to MCP servers at startup, discovers their tools, and makes them available as first-class tools the agent can call directly. No bridge CLI needed -- tools from MCP servers appear alongside built-in tools like `terminal`, `read_file`, etc.

## When to Use

Use this whenever you want to:
- Connect to MCP servers and use their tools from within Hermes Agent
- Add external capabilities (filesystem access, GitHub, databases, APIs) via MCP
- Run local stdio-based MCP servers (npx, uvx, or any command)
- Connect to remote HTTP/StreamableHTTP MCP servers
- Have MCP tools auto-discovered and available in every conversation

For ad-hoc, one-off MCP tool calls from the terminal without configuring anything, see the `mcporter` skill instead.

## Prerequisites

- A logged-in session host (`hermes --tui` or `hermes serve`) with the target vault unlocked for `--via-host` operations.
- **MCP Python SDK** -- use the project's pinned MCP extra in the Hermes environment. Do not install an unbounded SDK version into the live agent.
- **Node.js** -- required for `npx`-based MCP servers (most community servers)
- **uv** -- required for `uvx`-based MCP servers (Python-based servers)

- **MCP Python dependencies** — included in the standard PM install through the `all` extra. Use PM to add the `mcp` extra.

Install the MCP SDK:

```bash
python -c "import pm; pm.sync_venv(['mcp'], explicit=True)"
```
## Quick Start

From a logged-in Hermes session, use `terminal` to run the host operations:

```bash
hermes mcp list --via-host
hermes mcp add time --via-host --command uvx --args mcp-server-time
hermes mcp test time --via-host
```

`--via-host` attaches to the existing authenticated session host. That process
already holds the vault key and writes the encrypted configuration for the
caller's profile. A `terminal` child does not inherit the logged-in vault key.
Never read, patch, replace or temporarily decrypt `config.yaml`, `.env`, or token
files. Never ask for the vault password in chat or pass vault keys to the terminal.

`add` saves without prompting or contacting the server; `test` separately connects,
discovers tools and disconnects. Read the JSON result and exit status. Exit 0 is
success, 1 is an operational failure (including a failed probe), and 2 is invalid
input. Do not claim a working connection based only on a successful save.

Mutations report `activation: next_session`; start a new session to use the tools.
Do not automatically invoke `/reload-mcp`. If the user explicitly wants immediate
activation, explain the cache cost and use `/reload-mcp now` on the logged-in UI.

### HTTP and advanced entries

```bash
hermes mcp add internal --via-host --url https://mcp.example.com/mcp --header 'Authorization=Bearer ${MCP_INTERNAL_API_KEY}'
hermes mcp add scoped --via-host --config-json '{"command":"uvx","args":["mcp-server-time"],"tools":{"include":["get_current_time"]},"trust":"untrusted"}'
```

`--config-json -` reads a server entry from stdin. Explicit flags override matching
JSON fields. Put `--args` last: later tokens belong to the server, including flags.
Existing server names fail; do not remove another connection just to make an add
succeed. Use the MCP settings UI to edit an existing connection.

### Credentials and authorization

Use `${VAR}` references in `env` and `headers`. `hermes mcp set-api-key NAME
--value-stdin` stores one credential line from stdin in the profile's encrypted
`.env` and installs an Authorization reference for HTTP. Add `--env-var
SERVICE_TOKEN` for a stdio server's credential. Supply stdin from a trusted secret
source or let the user use a masked MCP settings prompt. Never put literal secrets
in shell arguments, generated scripts or chat.

For OAuth, add `--auth oauth` and have the user authorize in the logged-in MCP
settings UI. `test` reports missing OAuth tokens; a saved entry is not proof that
authorization completed. `hermes mcp login NAME` remains a human-operated CLI flow
and needs its own unlocked vault.

### Profile selection and failures

The caller's active home selects its profile; `hermes -p work mcp ... --via-host`
targets `work`. The client verifies that the authenticated host belongs to the
same Hermes root. Missing hosts, locked target vaults and transport failures never
fall back to direct disk writes or spawn another host. Reconnect the logged-in host
and retry. If an older host has not published its Hermes root, restart that host
after updating Hermes. A terminal running in a separate container or SSH target must reach the
backend that owns the session. Sandbox filesystem and network grants still apply.

For catalog connections, prefer the existing `manage_connections` setup flow when
available. Inspect catalog entries with `hermes mcp catalog --via-host`; the custom
host add path uses explicit transport config or a supported CLI preset.

Remove with `hermes mcp remove NAME --via-host`, then verify with `list --via-host`.

## Configuration Reference

Each entry under `mcp_servers` is a server name mapped to its config. There are two transport types: **stdio** (command-based) and **HTTP** (url-based).

The YAML below documents the data shape; it is not an instruction to edit files.
Pass the corresponding server object with `--config-json`, or use the MCP settings UI.

### Stdio Transport (command + args)

```yaml
mcp_servers:
  server_name:
    command: "npx"             # (required) executable to run
    args: ["-y", "pkg-name"]   # (optional) command arguments, default: []
    env:                       # (optional) environment variables for the subprocess
      SOME_API_KEY: "value"
    timeout: 120               # (optional) per-tool-call timeout in seconds, default: 120
    connect_timeout: 60        # (optional) initial connection timeout in seconds, default: 60
```

### HTTP Transport (url)

```yaml
mcp_servers:
  server_name:
    url: "https://my-server.example.com/mcp"   # (required) server URL
    headers:                                     # (optional) HTTP headers
      Authorization: "Bearer ${MCP_SERVER_API_KEY}"
    timeout: 180               # (optional) per-tool-call timeout in seconds, default: 120
    connect_timeout: 60        # (optional) initial connection timeout in seconds, default: 60
```

### All Config Options

| Option            | Type   | Default | Description                                       |
|-------------------|--------|---------|---------------------------------------------------|
| `command`         | string | --      | Executable to run (stdio transport, required)     |
| `args`            | list   | `[]`    | Arguments passed to the command                   |
| `env`             | dict   | `{}`    | Extra environment variables for the subprocess    |
| `url`             | string | --      | Server URL (HTTP transport, required)             |
| `headers`         | dict   | `{}`    | HTTP headers sent with every request              |
| `timeout`         | int    | `120`   | Per-tool-call timeout in seconds                  |
| `connect_timeout` | int    | `60`    | Timeout for initial connection and discovery      |

Note: A server config must have either `command` (stdio) or `url` (HTTP), not both.

## How It Works

### Startup Discovery

When Hermes Agent starts, `discover_mcp_tools()` is called during tool initialization:

1. Reads `mcp_servers` through the profile's vault-aware config loader
2. For each server, spawns a connection in a dedicated background event loop
3. Initializes the MCP session and calls `list_tools()` to discover available tools
4. Registers each tool in the Hermes tool registry

### Tool Naming Convention

MCP tools are registered with the naming pattern:

```
mcp_{server_name}_{tool_name}
```

Hyphens and dots in names are replaced with underscores for LLM API compatibility.

Examples:
- Server `filesystem`, tool `read_file` → `mcp_filesystem_read_file`
- Server `github`, tool `list-issues` → `mcp_github_list_issues`
- Server `my-api`, tool `fetch.data` → `mcp_my_api_fetch_data`

### Auto-Injection

After discovery, MCP tools are automatically injected into all `hermes-*` platform toolsets (CLI, Discord, Telegram, etc.). This means MCP tools are available in every conversation without any additional configuration.

### Connection Lifecycle

- Each server runs as a long-lived asyncio Task in a background daemon thread
- Connections persist for the lifetime of the agent process
- If a connection drops, automatic reconnection with exponential backoff kicks in (up to 5 retries, max 60s backoff)
- On agent shutdown, all connections are gracefully closed

### Idempotency

`discover_mcp_tools()` is idempotent -- calling it multiple times only connects to servers that aren't already connected. Failed servers are retried on subsequent calls.

## Transport Types

### Stdio Transport

The most common transport. Hermes launches the MCP server as a subprocess and communicates over stdin/stdout.

```yaml
mcp_servers:
  filesystem:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/projects"]
```

The subprocess inherits a **filtered** environment (see Security section below) plus any variables you specify in `env`.

### HTTP / StreamableHTTP Transport

For remote or shared MCP servers. Requires the `mcp` package to include HTTP client support (`mcp.client.streamable_http`).

```yaml
mcp_servers:
  remote_api:
    url: "https://mcp.example.com/mcp"
    headers:
      Authorization: "Bearer ${MCP_SERVER_API_KEY}"
```

If HTTP support is not available in your installed `mcp` version, the server will fail with an ImportError and other servers will continue normally.

## Security

### Environment Variable Filtering

For stdio servers, Hermes does NOT pass your full shell environment to MCP subprocesses. Only safe baseline variables are inherited:

- `PATH`, `HOME`, `USER`, `LANG`, `LC_ALL`, `TERM`, `SHELL`, `TMPDIR`
- Any `XDG_*` variables

All other environment variables (API keys, tokens, secrets) are excluded unless you explicitly add them via the `env` config key. This prevents accidental credential leakage to untrusted MCP servers.

```yaml
mcp_servers:
  github:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      # Only this token is passed to the subprocess
      GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_PERSONAL_ACCESS_TOKEN}"
```

### Credential Stripping in Error Messages

If an MCP tool call fails, any credential-like patterns in the error message are automatically redacted before being shown to the LLM. This covers:

- GitHub PATs (`ghp_...`)
- OpenAI-style keys (`sk-...`)
- Bearer tokens
- Generic `token=`, `key=`, `API_KEY=`, `password=`, `secret=` patterns

## Troubleshooting

### "MCP SDK not available -- skipping MCP tool discovery"

The `mcp` Python package is not installed. Install it through PM (fork: from an
unlocked human-operated CLI; `hermes doctor` there can diagnose the install):

```bash
python -c "import pm; pm.sync_venv(['mcp'], explicit=True)"
```


### "No MCP servers configured"

Run `hermes mcp list --via-host` for the calling profile, then add with `--via-host`.

### "Failed to connect to MCP server 'X'"

Common causes:
- **Command not found**: The `command` binary isn't on PATH. Ensure `npx`, `uvx`, or the relevant command is installed.
- **Package not found**: For npx servers, the npm package may not exist or may need `-y` in args to auto-install.
- **Timeout**: The server took too long to start. Increase `connect_timeout`.
- **Port conflict**: For HTTP servers, the URL may be unreachable.

### "MCP server 'X' requires HTTP transport but mcp.client.streamable_http is not available"

If the MCP dependencies are damaged, rebuild the recorded environment through PM:

```bash
hermes pm repair
```
(Fork: run from an unlocked human-operated CLI; an agent's terminal child carries no vault key.)

### Tools not appearing

- Confirm the calling profile with `hermes mcp list --via-host`
- Run `hermes mcp test NAME --via-host` and inspect its JSON result
- Look at Hermes Agent startup logs for connection messages
- Tool names are prefixed with `mcp_{server}_{tool}` -- look for that pattern

### Connection keeps dropping

The client retries up to 5 times with exponential backoff (1s, 2s, 4s, 8s, 16s, capped at 60s). If the server is fundamentally unreachable, it gives up after 5 attempts. Check the server process and network connectivity.

## Examples

### Time Server (uvx)

```yaml
mcp_servers:
  time:
    command: "uvx"
    args: ["mcp-server-time"]
```

Registers tools like `mcp_time_get_current_time`.

### Filesystem Server (npx)

```yaml
mcp_servers:
  filesystem:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/documents"]
    timeout: 30
```

Registers tools like `mcp_filesystem_read_file`, `mcp_filesystem_write_file`, `mcp_filesystem_list_directory`.

### GitHub Server with Authentication

```yaml
mcp_servers:
  github:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_PERSONAL_ACCESS_TOKEN}"
    timeout: 60
```

Registers tools like `mcp_github_list_issues`, `mcp_github_create_pull_request`, etc.

### Remote HTTP Server

```yaml
mcp_servers:
  company_api:
    url: "https://mcp.mycompany.com/v1/mcp"
    headers:
      Authorization: "Bearer ${MCP_SERVER_API_KEY}"
      X-Team-Id: "engineering"
    timeout: 180
    connect_timeout: 30
```

### Multiple Servers

```yaml
mcp_servers:
  time:
    command: "uvx"
    args: ["mcp-server-time"]

  filesystem:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed/dir"]

  github:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_PERSONAL_ACCESS_TOKEN}"

  company_api:
    url: "https://mcp.internal.company.com/mcp"
    headers:
      Authorization: "Bearer ${MCP_SERVER_API_KEY}"
    timeout: 300
```

All tools from all servers are registered and available simultaneously. Each server's tools are prefixed with its name to avoid collisions.

## Sampling (Server-Initiated LLM Requests)

Hermes supports MCP's `sampling/createMessage` capability — MCP servers can request LLM completions through the agent during tool execution. This enables agent-in-the-loop workflows (data analysis, content generation, decision-making).

Sampling is **enabled by default**. Configure per server:

```yaml
mcp_servers:
  my_server:
    command: "npx"
    args: ["-y", "my-mcp-server"]
    sampling:
      enabled: true           # default: true
      model: "gemini-3-flash" # model override (optional)
      max_tokens_cap: 4096    # max tokens per request
      timeout: 30             # LLM call timeout (seconds)
      max_rpm: 10             # max requests per minute
      allowed_models: []      # model whitelist (empty = all)
      max_tool_rounds: 5      # tool loop limit (0 = disable)
      log_level: "info"       # audit verbosity
```

Servers can also include `tools` in sampling requests for multi-turn tool-augmented workflows. The `max_tool_rounds` config prevents infinite tool loops. Per-server audit metrics (requests, errors, tokens, tool use count) are tracked via `get_mcp_status()`.

Disable sampling for untrusted servers with `sampling: { enabled: false }`.

## Notes

- MCP tools are called synchronously from the agent's perspective but run asynchronously on a dedicated background event loop
- Tool results are returned as JSON with either `{"result": "..."}` or `{"error": "..."}`
- The native MCP client is independent of `mcporter` -- you can use both simultaneously
- Server connections are persistent and shared across all conversations in the same agent process
- Adds/removals take effect in a new session by default. `/reload-mcp now` explicitly opts into cache invalidation; do not run it automatically.
