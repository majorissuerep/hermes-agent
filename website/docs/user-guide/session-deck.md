---
title: "Session Deck"
description: "One place for every open session: sessions outlive their terminals, stay open until closed, and can be messaged or closed from anywhere."
---

# Session Deck

Every session you start stays **open until you close it**. Closing the terminal, a crash, `/quit`, or a
reboot only *detaches* it; the conversation, its place in the deck and its handle (`#7`) survive. The deck is
the single view of all of them, across every profile on the machine, and any session, script or
terminal can message or close any other.

```
$ hermes deck ls
   ref  title                     state     where           msgs  active
●  #4   Benchmark sweep           running   /srv/bench        12     now
◐  #7   Refactor auth middleware  waiting   ~/src/api         31      2m
○  #2   Session deck design       idle      ~/hermes-agent     8      5m
◌  work#3  Invoice parser         detached  ~/work/inv        40      1h
·  #1   Old research              dormant   ~/notes           64      2d
5 open  ·  ◐ 1 waiting  ·  ● 1 running  ·  ○ 1 idle  ·  ◌ 1 detached  ·  · 1 dormant
```

| Glyph | State | Meaning |
|---|---|---|
| `◐` | waiting | blocked on you (an approval or a question) |
| `●` | running | a turn is in progress |
| `○` | idle | loaded, a terminal is attached |
| `◌` | detached | loaded and live, no terminal attached |
| `·` | dormant | open but not loaded; the next message or attach wakes it |

## How it works

`hermes --tui` no longer runs the agent inside your terminal. It attaches to the **session host**, one
machine-level `hermes serve` that owns every deck session and starts on demand. The terminal is just a
client, so losing it loses nothing. Several terminals can attach to the same session at once.

The host keeps sessions loaded while they are in use. An idle session is unloaded after
`HERMES_TUI_SESSION_TTL_S` (default 6 h) or when the live-session cap is reached; it becomes *dormant*,
not closed. On a host restart every open session comes back dormant.

The registry is a table in each profile's `state.db`. With the secure vault enabled it is encrypted at
rest like the rest of that database (SQLCipher). Rows hold references only (conversation id, working
directory, state), never transcripts. The host snapshots runtime states every few seconds.

## Commands

| | |
|---|---|
| `hermes deck` | the interactive deck (TUI) |
| `hermes deck ls [--profile P] [--closed] [--json]` | list open sessions |
| `hermes deck attach '#7'` | open a session in this terminal |
| `hermes deck send '#7' "text"` | queue a message as that session's next turn |
| `hermes deck send '#7' --wait 300 "text"` | …and print its reply |
| `hermes deck peek '#7' [-n 8]` | latest messages |
| `hermes deck interrupt '#7'` | stop its running turn |
| `hermes deck close '#7'` | close it for good |

`#7` is handle 7 in your current profile; `work#3` addresses profile `work`. Quote references in the
shell (`#` starts a comment).

Inside a session:

- **`/close`** ends the session and removes it from the deck.
- **`/quit`**, Ctrl-D and closing the window only detach it.
- **Ctrl+X** or **`/sessions`** opens the deck overlay.

### The deck view

`↑↓`/`jk` move · `⏎` open · `n` new session · `s` send a message to the selected session · `i` stop its
turn · `x x` close it · `/` fuzzy filter · `q` quit (sessions stay open). The top line counts sessions
by state, a `⚡ needs you` strip lists sessions waiting on you, and the bottom pane previews the selected
session's latest messages.

## Sessions talking to sessions

A message never interrupts a turn in progress. It is queued durably and runs as the target's **next
user turn** once it is idle. The target sees a one-line header naming the sender
(`[Session deck — message from session #2 "Refactor auth"]`). A dormant target is woken to receive it.

Agents use the same commands through their terminal (the bundled `session-deck` skill teaches them).
The sending session is identified automatically, and a session cannot message, interrupt or close
itself through the deck.

## Classic CLI

The prompt_toolkit REPL (`hermes` without `--tui`) keeps its agent in-process, so it still ends with
its terminal. Its conversation stays open in the deck (dormant) and `hermes deck attach` resumes it in
the host. While it runs, it receives deck messages at idle and exits when the deck closes it.

## Configuration

```yaml
sessions:
  host: true   # false: `hermes --tui` runs its own backend and the session ends with the terminal
```

Launches with per-run overrides the shared host cannot apply (`-m/--model`, `--provider`, `--toolsets`,
`--skills`, `--worktree`, `--checkpoints`, `--max-turns`, `-v/-Q`) use a private backend as before.

With the secure vault, the launcher hands its unlocked key to the detached host over an inherited pipe;
no password is placed in the environment or on disk. On Windows the host needs
`HERMES_MASTER_PASSWORD` or a key file instead.
