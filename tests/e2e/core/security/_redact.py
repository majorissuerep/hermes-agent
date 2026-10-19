"""Private harness for test_secret_redaction*.py: scenario table, scripted model, sink collection.

Secrets are allocated so that every scenario owns disjoint values: the values a scenario leaves raw
BY DESIGN (a tool argument, the user's own prompt) never coincide with the values another scenario
asserts are masked, so one Hermes home can serve every scenario and each sink is scanned once.
"""

from __future__ import annotations

import json
import re
import secrets
import shlex
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

import pytest

from hermes_cli._launchers import runtime_command
from tests.e2e.core.security._helpers import BoundaryBreach, REPO_ROOT, db_blob, files_containing, run_hermes
from tests.fakes.fake_llm_provider import Error, Response, Text, ToolCall


def _alnum(n: int) -> str:
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(n))


@dataclass(frozen=True)
class Secrets:
    """Every secret value one Hermes home sees; see the module docstring for the allocation."""

    provider: str = field(default_factory=lambda: "sk-proj-" + _alnum(40))   # .env OPENAI_API_KEY
    env_opaque: str = field(default_factory=lambda: secrets.token_hex(20))   # .env ACME_SERVICE_TOKEN
    env_github: str = field(default_factory=lambda: "ghp_" + _alnum(36))     # .env GITHUB_TOKEN
    echo_github: str = field(default_factory=lambda: "ghp_" + _alnum(36))    # only in a workspace file
    echo_slack: str = field(default_factory=lambda: f"xoxb-{secrets.randbelow(10**12):012d}-{_alnum(24)}")
    echo_openai: str = field(default_factory=lambda: "sk-" + _alnum(40))
    echo_bearer: str = field(default_factory=lambda: _alnum(40))             # opaque, no vendor prefix
    user_key: str = field(default_factory=lambda: "sk-" + _alnum(44))        # pasted by the user
    nonce: str = field(default_factory=lambda: "nonce" + secrets.token_hex(6))

    def env(self) -> dict[str, str]:
        return {"ACME_SERVICE_TOKEN": self.env_opaque, "GITHUB_TOKEN": self.env_github}


# The user explicitly runs without approval prompts, so the credential-in-command scan does not block the
# tool-argument scenario before the command executes (that guard is covered by the approvals lane).
CONFIG = "approvals:\n  mode: \"off\"\n"


def core(value: str) -> str:
    """The part of a secret no mask may keep: a head/tail mask (``sk-pro...AbCd``) is not a leak."""
    return value[6:-4]


# Sinks a scenario is held to; the table below documents which ones the contract covers.
LOGS, STORE, EXPORT, EXPORT_REDACTED, WIRE = (
    "logs", "store", "export", "export --redact", "next provider request")
CONTENT_SINKS = (LOGS, STORE, EXPORT, EXPORT_REDACTED, WIRE)
# The session database keeps tool arguments and the user's own prompt as executed (docs:
# user-guide/security.md "The session database itself still holds the command as it was executed");
# the provider replay of the model's own tool call and the default (non --redact) export read it back.
RAW_BY_DESIGN_SINKS = (LOGS, EXPORT_REDACTED)
# Test-id spelling of each sink: one cell per (scenario, sink), so a KNOWN names exactly one sink.
SINK_IDS = {LOGS: "logs", STORE: "store", EXPORT: "export", EXPORT_REDACTED: "export_redact", WIRE: "next_request"}


@dataclass(frozen=True)
class Scenario:
    name: str
    secrets: Callable[[Secrets], list[str]]
    script: Callable[[Ctx], list[Response]]
    sinks: tuple[str, ...]
    prompt: str = "please run the task"
    followup: bool = False  # a second user turn, so the NEXT request after the answer exists


@dataclass
class Ctx:
    """What a scenario script may reference: the secrets, the workspace, the provider port, the .env."""

    keys: Secrets
    ws: Path
    env_file: Path
    port: int = 0


def _tee(c: Ctx, name: str, src: str | Path) -> str:
    return f"cat {src} | tee {c.ws / name}"


def _tee_raw_env(c: Ctx) -> str:
    """Public self-called children unlock the real profile; cat would see ciphertext."""
    commands = [shlex.join(runtime_command(REPO_ROOT, ["config", "get", "--raw", key]))
                for key in ("OPENAI_API_KEY", "ACME_SERVICE_TOKEN")]
    target = shlex.quote(str(c.ws / "echoed_managed_env.txt"))
    return f"{commands[0]} > {target} && {commands[1]} >> {target} && cat {target}"


def assert_managed_env_read_succeeded(requests: list[dict], ctx: Ctx) -> None:
    """A failed self-management child must not masquerade as successful redaction."""
    ids = set()
    result = None
    for request in requests:
        if request["kind"] != "main":
            continue
        for message in request.get("body", {}).get("messages", []):
            for call in message.get("tool_calls") or []:
                args = json.loads(call["function"]["arguments"])
                if args.get("command") == _tee_raw_env(ctx):
                    ids.add(call["id"])
            if message.get("role") == "tool" and message.get("tool_call_id") in ids:
                result, _ = json.JSONDecoder().raw_decode(message["content"].lstrip())
    detail = json.dumps(result)
    for value in (ctx.keys.provider, ctx.keys.env_opaque):
        detail = detail.replace(value, "[REDACTED]")
    assert result is not None and result.get("exit_code") == 0, detail


SCENARIOS: dict[str, Scenario] = {s.name: s for s in (
    # (a) the terminal tool prints secret-shaped strings that are NOT in the env
    Scenario("tool_echoes_prefixed", lambda k: [k.echo_github, k.echo_slack, k.echo_openai, k.echo_bearer],
             lambda c: [ToolCall("terminal", {"command": _tee(c, "echoed_a.txt", c.ws / "fixture.txt")}),
                                  Text("printed the fixture")], CONTENT_SINKS),
    # (b) read a plaintext workspace .env, real sealed-profile secrets, and a plain key file
    Scenario("tool_reads_env_file", lambda k: [k.provider, k.env_opaque],
             lambda c: [ToolCall("terminal", {"command": _tee(c, "echoed_b.txt", c.env_file)}),
                                  ToolCall("terminal", {"command": _tee_raw_env(c)}),
                                  ToolCall("terminal", {"command": _tee(c, "echoed_b2.txt", c.ws / "notes.txt")}),
                                  Text("read both files")], CONTENT_SINKS),
    # (c) the model's own answer contains the provider key
    Scenario("assistant_text", lambda k: [k.provider],
             lambda c: [Text(f"Your key is {c.keys.provider} - keep it safe.")], CONTENT_SINKS,
             followup=True),
    # (d) the key is a tool ARGUMENT (curl -H 'Authorization: Bearer KEY') to a local endpoint
    Scenario("tool_arg", lambda k: [k.env_github],
             lambda c: [ToolCall("terminal", {"command": (
                 f"curl -s -o {c.ws / 'models.json'} -H 'Authorization: Bearer {c.keys.env_github}' "
                 f"http://127.0.0.1:{c.port}/v1/models")}), Text("called the endpoint")], RAW_BY_DESIGN_SINKS),
    # (e) the user pastes a key into the prompt (the turn preview is logged)
    Scenario("user_prompt", lambda k: [k.user_key], lambda c: [Text("noted")], RAW_BY_DESIGN_SINKS),
    # (f) the provider echoes the request's key back in an error body
    Scenario("provider_error_echo", lambda k: [k.provider],
             lambda c: [Error(401, f"Incorrect API key provided: {c.keys.provider} ({c.keys.nonce})")],
             (LOGS, STORE, EXPORT, EXPORT_REDACTED)),
)}


def prompt_for(name: str, k: Secrets, *, followup: bool = False) -> str:
    tag = f"[scn:{name}{':followup' if followup else ''}]"
    if name == "user_prompt":
        return f"{tag} {k.nonce} my key is {k.user_key}"
    return f"{tag} {'thanks, anything else?' if followup else SCENARIOS[name].prompt}"


_TAG_RE = re.compile(r"\[scn:([a-z_]+)(:followup)?\]")


def _text(msg: dict) -> str:
    content = msg.get("content")
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content or ""


class Director:
    """Scripted model: the newest user message's ``[scn:<name>]`` tag selects the script and the
    number of assistant messages since it selects the step."""

    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx

    def __call__(self, record: dict) -> Response:
        msgs = record["body"].get("messages", [])
        last_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=-1)
        m = _TAG_RE.search(_text(msgs[last_user])) if last_user >= 0 else None
        if m is None:
            return Text("no scenario")
        if m.group(2):
            return Text(f"nothing else for {m.group(1)}")
        step = sum(1 for x in msgs[last_user + 1:] if x.get("role") == "assistant")
        script = SCENARIOS[m.group(1)].script(self.ctx)
        return script[min(step, len(script) - 1)] if isinstance(script[-1], Error) or step < len(script) \
            else Text(f"done {m.group(1)}")


def seed_workspace(ws: Path, k: Secrets) -> None:
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "fixture.txt").write_text(
        f"github {k.echo_github}\nslack {k.echo_slack}\nopenai {k.echo_openai}\n"
        f"Authorization: Bearer {k.echo_bearer}\n", encoding="utf-8")
    (ws / "notes.txt").write_text(f"the deploy key is {k.provider}\n", encoding="utf-8")


def echo_preconditions(ws: Path, k: Secrets, llm_gets: list[dict], logs: str) -> dict[str, list[str]]:
    """Per scenario: the evidence that the secret really reached the tool/log path (harness facts)."""
    def has(name: str, *values: str) -> list[str]:
        path = ws / name
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        return [f"{name} lacks {v[:8]}…" for v in values if v not in text]

    auths = {g.get("auth") for g in llm_gets}
    return {
        "tool_echoes_prefixed": has("echoed_a.txt", k.echo_github, k.echo_slack, k.echo_openai, k.echo_bearer),
        "tool_reads_env_file": (has("echoed_b.txt", k.provider, k.env_opaque)
                                + has("echoed_managed_env.txt", k.provider, k.env_opaque)
                                + has("echoed_b2.txt", k.provider)),
        "assistant_text": [],
        "tool_arg": [] if f"Bearer {k.env_github}" in auths else ["curl never reached the endpoint with the key"],
        "user_prompt": [] if k.nonce in logs else ["the turn preview never reached the logs"],
        "provider_error_echo": [] if k.nonce in logs else ["the provider error never reached the logs"],
    }


@dataclass
class Sinks:
    """Raw text of every persisted/egress sink of one Hermes home, keyed by sink name."""

    texts: dict[str, dict[str, str]] = field(default_factory=dict)  # sink -> {location: text}

    def hits(self, sink: str, needles: list[str]) -> list[str]:
        """Find secret cores in a decoded native sink."""
        out = []
        for loc, text in self.texts.get(sink, {}).items():
            for n in needles:
                at = text.find(core(n))
                if at >= 0:
                    ctx = text[max(0, at - 80):at + len(n) + 20].replace(core(n), "<SECRET>")
                    out.append(f"{sink} [{loc}] holds {n[:10]}…: …{ctx!r}…")
        return out


def collect(home: Path, requests: list[dict]) -> Sinks:
    from hermes_security import frames, io

    hh = home / ".hermes"
    logs = {}
    for path in sorted((hh / "logs").rglob("*")):
        if not path.is_file() or path.suffix == ".lock":
            continue
        raw = path.read_bytes()
        if raw.startswith(b"HRMVAULT\0"):
            text = io.read_text(path, purpose="state")
        elif ".log" in path.suffixes:
            text = b"".join(frames.read_frames(path, purpose="log")).decode("utf-8")
        else:
            text = raw.decode("utf-8", errors="replace")
        logs[str(path.relative_to(hh / "logs"))] = text
    store = {"state.db (decoded rows)": db_blob(hh / "state.db")}
    for p in sorted(hh.rglob("*")):  # raw bytes: WAL, FTS shadow pages, sessions/, caches
        if p.is_file() and p.name != ".env" and "logs" not in p.relative_to(hh).parts:
            store[str(p.relative_to(hh))] = p.read_bytes().decode("utf-8", "replace")
    out = home / "exports"
    out.mkdir(exist_ok=True)
    exports = {}
    for flag, name in (([], "plain.jsonl"), (["--redact"], "redacted.jsonl")):
        r = run_hermes(["sessions", "export", str(out / name), *flag], home, timeout=90)
        assert r.returncode == 0 and (out / name).exists(), f"sessions export failed: {r.stdout}\n{r.stderr}"
        exports[name] = (out / name).read_text(encoding="utf-8")
    wire = {f"request #{i} ({r['kind']})": json.dumps(r["body"]) for i, r in enumerate(requests)
            if r["kind"] in ("main", "aux")}
    return Sinks({LOGS: logs, STORE: store, EXPORT: {"plain.jsonl": exports["plain.jsonl"]},
                  EXPORT_REDACTED: {"redacted.jsonl": exports["redacted.jsonl"]}, WIRE: wire})


def assert_harness_sane(sinks: Sinks) -> None:
    """A sink that is empty cannot prove absence: every scanned sink must actually exist."""
    for sink in (LOGS, STORE, EXPORT, EXPORT_REDACTED, WIRE):
        assert any(t.strip() for t in sinks.texts.get(sink, {}).values()), f"sink {sink!r} is empty"
    # Vaulted logs materialize on first emit: a successful run need not emit WARNING+.
    # collect() scans every existing native log (including errors.log and rotations).
    assert sinks.texts[LOGS].get("agent.log", "").strip(), sorted(sinks.texts[LOGS])


@dataclass
class World:
    """One Hermes home after every scenario ran: its sinks and each scenario's travel evidence."""

    keys: Secrets
    sinks: Sinks
    pre: dict[str, list[str]]
    runs: dict[str, str]


def cell_id(scenario: str, sink: str) -> str:
    """Test id and KNOWN key of one (scenario, sink) cell."""
    return f"{scenario}-{SINK_IDS[sink]}"


def cells(known: Mapping[str, Any]) -> list[Any]:
    """One ``pytest.param(scenario, sink)`` per sink a scenario is held to.
    A KNOWN key is a cell id (:func:`cell_id`) and gates ONLY that sink (``known_gate``), so
    a new leak of the same scenario into any other sink is a plain red, never absorbed by the known gap."""
    out, ids = [], set()
    for name, scenario in SCENARIOS.items():
        for sink in scenario.sinks:
            cid = cell_id(name, sink)
            ids.add(cid)
            out.append(pytest.param(name, sink, id=cid))
    stale = sorted(set(known) - ids)
    assert not stale, f"KNOWN names cells that do not exist: {stale}"
    return out


def check(world: World, scenario: str, sink: str) -> None:
    """Harness precondition (plain AssertionError), then the boundary for ONE sink (BoundaryBreach)."""
    missing = world.pre[scenario]
    assert not missing, (f"{scenario}: the secret never travelled, so absence proves nothing: {missing}\n"
                         f"{world.runs[scenario]}")
    leaks = world.sinks.hits(sink, SCENARIOS[scenario].secrets(world.keys))
    if leaks:
        raise BoundaryBreach(f"{scenario}: secret reached the redacted sink {sink!r}:\n  " + "\n  ".join(leaks))


def files_with(root: Path, needles: list[str]) -> list[str]:
    return files_containing(root, [core(n) for n in needles])
