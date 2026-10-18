# Native terminal commands

Use `hermes --help` and `hermes <command> --help` for the installed version.
There is one interactive interface: the local Ink TUI. There is no Desktop,
messaging gateway, remote-management server, inbound MCP server or ACP server.

## Start and resume

```sh
hermes
hermes --continue
hermes --resume SESSION_ID
hermes chat -q "One bounded task"
hermes --profile NAME chat -q "A profile-specific task"
```

`--model`, `--provider`, `--toolsets`, `--skills` and `--max-turns` configure an
invocation. `--native` selects primary-buffer TUI rendering. Do not pass removed
`--cli` flags. `--safe-mode` is for customization diagnosis; pass an explicit
provider/model when ignoring profile configuration.

Inside the TUI, `/model` opens the picker and `/model <model>` switches the session.
`/reasoning` controls effort. Use `/help` for actual supported slash commands.
History and cached prompt prefixes must survive model switches.

## Manage locally

```sh
hermes model
hermes tools
hermes config get agent.max_turns
hermes config set agent.max_turns 30
hermes update --check
hermes update --backup
hermes doctor
hermes pm repair
hermes secure-vault status
hermes secure-vault repair
hermes backup
hermes import --help
```

Ordinary local commands inside an unlocked Hermes session reuse scoped authority.
Never forward a passphrase or key, edit ciphertext, or use another profile to bypass
unlocking. Read `encrypted-self-management.md` for repair, update and offline export.

## Extensions, history and local workers

| Command | Purpose |
| --- | --- |
| `hermes skills --help` | Skills and bundles |
| `hermes plugins --help` | Explicit opt-in plugins |
| `hermes mcp --help` | Outgoing stdio/HTTP tool connections |
| `hermes sessions --help` | Local history |
| `hermes profile --help` | Local profile lifecycle and migration |
| `hermes cron --help` | Jobs, explicit local tick/run and run history |
| `hermes kanban --help` | Local board and terminal workers |
| `hermes curator --help` | Skill maintenance and rollback |
| `hermes logs --help` | Native agent/error logs |

No `--via-host` management route is needed: use the ordinary local CLI. Tool and
system-prompt changes normally activate in a new session, preserving prompt caching.
