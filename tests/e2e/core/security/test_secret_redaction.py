"""Secrets are redacted before native CLI storage, exports and provider replay.

Contract under test (``security.redact_secrets``, on by default; website/docs/user-guide/configuration.md
§ Security, hermes_logging.py, user-guide/sessions.md § Export Sessions):

* tool output is redacted before it enters the conversation, so it never reaches state.db (any table,
  FTS, WAL), a session export, or the next provider request; a read of a secret-bearing file (``.env``)
  masks credential-shaped assignments whatever the value looks like;
* the model's own answer is redacted at the storage boundary (history / state.db / exports / replay);
* native log streams go through ``RedactingFormatter`` before encrypted append;
* ``hermes sessions export --redact`` masks message content AND tool-call arguments.

NOT COVERED (raw by design, so not asserted): a tool argument and the user's own prompt stay exactly as
executed in state.db, in the default (non ``--redact``) export and in the provider replay of the
conversation. ``--redact`` is the opt-in export mode for them (acfefa4fdac). Terminal stdout of
``chat -q`` is also not scanned: it is the user's own screen. Every scenario first proves the secret
really travelled (the tool echoed it into a workspace file, curl hit the endpoint with it, the log recorded
the turn) before asserting absence.
"""

from __future__ import annotations


import pytest

from tests.e2e.core._pending_fixes import known_gate
from tests.e2e.core.security._helpers import BoundaryBreach, run_hermes, write_home
from tests.e2e.core.security._redact import (
    CONFIG, SCENARIOS, Ctx, Director, Secrets, World, assert_harness_sane, assert_managed_env_read_succeeded,
    cell_id, cells, check, collect,
    echo_preconditions, prompt_for, seed_workspace,
)
from tests.fakes.fake_llm_provider import FakeLLMServer
from tests.e2e.vault_home import connect_state

pytestmark = pytest.mark.platforms("posix")

KNOWN: dict[str, tuple[str, str]] = {}  # cell id ``<scenario>-<sink>`` -> (pattern, "#issue symptom")

@pytest.fixture(scope="module")
def cli_world(tmp_path_factory) -> World:
    root = tmp_path_factory.mktemp("redact-cli")
    home, ws, keys = root / "home", root / "ws", Secrets()
    seed_workspace(ws, keys)
    env_file = ws / "fixture" / ".env"
    env_file.parent.mkdir()
    env_file.write_text(f"OPENAI_API_KEY={keys.provider}\nACME_SERVICE_TOKEN={keys.env_opaque}\n")
    ctx = Ctx(keys, ws, env_file)
    with FakeLLMServer(Director(ctx), api_key=keys.provider, record_get=True) as llm:
        ctx.port = llm.port
        write_home(home / ".hermes", llm.base_url, api_key=keys.provider, env=keys.env(), config=CONFIG)
        runs: dict[str, str] = {}
        for name, scenario in SCENARIOS.items():
            r = run_hermes(["chat", "-q", prompt_for(name, keys), "-Q"], home, cwd=ws, timeout=150)
            runs[name] = f"rc={r.returncode}\n{r.stdout[-1500:]}\n{r.stderr[-2500:]}"
            if scenario.followup:
                with connect_state(home / ".hermes" / "state.db") as db:
                    row = db.execute("SELECT id FROM sessions ORDER BY started_at DESC, rowid DESC LIMIT 1").fetchone()
                sid = row[0] if row else None
                assert r.returncode == 0 and sid, f"{name}: first turn failed\n{runs[name]}"
                r2 = run_hermes(["chat", "-q", prompt_for(name, keys, followup=True), "-Q", "--resume", sid],
                                home, cwd=ws, timeout=150)
                assert r2.returncode == 0, f"{name}: follow-up turn failed\n{r2.stdout}\n{r2.stderr}"
            elif name != "provider_error_echo":
                assert r.returncode == 0, f"{name}: turn failed\n{runs[name]}"
            if name == "tool_reads_env_file":
                assert_managed_env_read_succeeded(list(llm.requests), ctx)
        sinks = collect(home, list(llm.requests))
        gets = [r for r in llm.requests if r["kind"] == "get"]
    assert_harness_sane(sinks)
    logs = "\n".join(sinks.texts["logs"].values())
    return World(keys, sinks, echo_preconditions(ws, keys, gets, logs), runs)


@pytest.mark.parametrize("scenario, sink", cells(KNOWN))
def test_cli_turn_never_persists_or_replays_a_secret(cli_world: World, scenario: str, sink: str) -> None:
    with known_gate(KNOWN, cell_id(scenario, sink), raises=BoundaryBreach):
        check(cli_world, scenario, sink)
