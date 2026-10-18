# In-session commands

Use `/help` in the local TUI. The installed `hermes_cli/commands.py` registry and
private stdio handlers are authoritative; do not suggest removed messaging,
Desktop, handoff, restart or platform-management commands.

## Conversation

- `/new` / `/reset`: fresh session and tool surface.
- `/sessions` / `/resume`: browse or resume local history.
- `/title`, `/branch`, `/retry`, `/undo`: conversation management.
- `/compress`: explicit context compression; preserve active requests and tool pairs.
- `/status` / `/context` / `/usage`: inspect actual model and context pressure.

## Model and settings

- `/model`: picker; `/model <model>`: session-scoped switch retaining history.
- `/reasoning`: effort/display controls supported by the chosen connection.
- `/config`: inspect configuration through its encrypted reader.
- `/tools`, `/skills`, `/plugins`: local capability selection.
- `/skin`: TUI palette; `/timestamps`: message timestamp display.

Tool/system-prompt changes normally wait for the next session. Do not reload
memory, tools or prompts mid-conversation on your own. A model switch must not
rewrite past messages or silently discard a user turn.

## Work and local maintenance

- `/agents`: local delegated tasks.
- `/queue` / `/steer`: queue or steer a busy turn.
- `/goal`, `/heartbeat`, `/loop`: local session work controls.
- `/cron`, `/kanban`: local durable job/board operations.
- `/snapshot` / `/rollback`: state/filesystem recovery with their distinct scopes.
- `/update`: native update, not service restart.
- `/quit` / `/exit`: close the local session and revoke its unlock authority.

For headless self-management, use ordinary `hermes ...` commands through terminal
rather than emitting slash commands to a shell. See `cli-reference.md`.
