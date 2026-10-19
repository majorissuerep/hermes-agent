"""Fixture-only native lifecycle observer; never observes prompts, grants or credentials."""
from __future__ import annotations

import json
from pathlib import Path


def install_observer(home: Path, evidence: Path) -> Path:
    from hermes_cli.config import read_raw_config, save_config

    destination = evidence / "child-identities.jsonl"
    destination.touch(mode=0o600, exist_ok=False)
    plugin = home / "plugins" / "active-update-observer"
    plugin.mkdir(parents=True, exist_ok=False)
    (plugin / "plugin.yaml").write_text(
        'name: active-update-observer\nversion: "0.1.0"\ndescription: Fixture identity witness\n'
        'provides_hooks: [subagent_start]\n')
    (plugin / "__init__.py").write_text('''import json
import os
import threading

_LOCK = threading.Lock()
_PATH = ''' + repr(str(destination)) + '''

def observe(parent_session_id, child_session_id, child_subagent_id):
    values = (parent_session_id, child_session_id, child_subagent_id)
    if not all(isinstance(value, str) and 0 < len(value) <= 200 for value in values):
        raise ValueError("invalid native child identity")
    row = dict(zip(("parent_session_id", "child_session_id", "child_subagent_id"), values))
    with _LOCK:
        if os.stat(_PATH).st_size > 4096:
            raise RuntimeError("fixture observer limit exceeded")
        descriptor = os.open(_PATH, os.O_WRONLY | os.O_APPEND)
        try:
            os.write(descriptor, (json.dumps(row) + "\\n").encode())
        finally:
            os.close(descriptor)

def register(ctx):
    ctx.register_hook("subagent_start", observe)
''')
    config = read_raw_config()
    enabled = config.setdefault("plugins", {}).setdefault("enabled", [])
    enabled.append("active-update-observer")
    save_config(config)
    return destination


def read_identities(path: Path) -> list[dict]:
    if path.stat().st_size > 8192:
        raise RuntimeError("fixture observer exceeded bounded evidence")
    return [json.loads(line) for line in path.read_text().splitlines()]
