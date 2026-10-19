"""Actual Ink session survives its own real terminal-child source update.

Run only in an isolated install prepared by the cold coordinator. The install must
start at a native terminal baseline, with its controlled origin already publishing
--candidate. stdin supplies {"password": ..., "api_key": ...}; neither is logged.
This is real Ink keyboard/PTY evidence, not only a private-runtime RPC transcript.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

from scripts.e2e.active_update_proof import GET_COMMAND, SET_COMMAND, UPDATE_COMMAND, paired_calls, prefix_hash, verify_calls


class Acceptance:
    def __init__(self, args, secret):
        if not isinstance(secret.get("password"), str) or not secret["password"]:
            raise ValueError("stdin must supply the synthetic fixture password")
        if args.kind == "live" and not secret.get("api_key"):
            raise ValueError("live transcript sanitization requires the dedicated API key on stdin")
        self.args, self.password = args, secret["password"]
        self.root = args.root.resolve()
        self.code = args.install_root.resolve()
        self.home = args.home.resolve()
        self.evidence = args.evidence.resolve()
        for path in (self.code, self.home, self.evidence, args.launcher.resolve()):
            if not path.is_relative_to(self.root) or path == self.root:
                raise ValueError("install, home and evidence must be children of the disposable root")
        if self.root == Path.home().resolve() or self.home == Path.home().resolve():
            raise ValueError("a real user home is not a disposable acceptance root")
        self.evidence.mkdir(parents=True, exist_ok=False)
        self.workspace = self.root / ("active-work-" + secrets.token_hex(6))
        self.workspace.mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("HERMES_VAULT_", "PYTEST_")) and key not in {
                        "PYTHONPATH", "PYTHONHOME", "HERMES_PYTHON", "HERMES_PYTHON_SRC_ROOT",
                        "HERMES_TUI_DIR", "HERMES_INSTALL_ROOT", "HERMES_CWD"}}
        self.tag = "active-update-" + secrets.token_hex(16)
        self.env.update(HOME=str(self.home.parent), HERMES_HOME=str(self.home), PYTHONUNBUFFERED="1",
                        PARITY_TREE_TAG=self.tag, LANG="C.UTF-8",
                        PATH=os.pathsep.join((str(args.launcher.resolve().parent), self.env.get("PATH", ""))))
        os.environ["HERMES_HOME"] = str(self.home)
        from hermes_security import vault
        vault.unlock(self.home, self.password)
        self.vault = vault
        self.secret_values = [secret.get("api_key", "")]
        self.receipt = {"kind": args.kind, "surface": "actual-public-ink-pty", "baseline": args.baseline,
                        "candidate": args.candidate, "status": "running", "cleanup_verified": False,
                        "launcher": str(args.launcher.resolve()),
                        "pm_runtime_dir": self.env.get("HERMES_RUNTIME_DIR"),
                        "pm_node": self.env.get("HERMES_NODE")}
        self.terminal = None
        self.parent_id = None
        from tests.e2e.core.upgrade import _process_reaper
        self.reaper = _process_reaper
        self.reaper.adopt_orphans()

    def head(self):
        return subprocess.check_output(
            ["git", "-C", str(self.code), "rev-parse", "HEAD"], text=True, timeout=30,
        ).strip()

    def messages(self):
        from hermes_security import sqlite
        path = self.home / "state.db"
        if not path.exists():
            return []
        with sqlite.connect(f"file:{path}?mode=ro", uri=True) as db:
            db.row_factory = sqlite.row_class()
            return [dict(row) for row in db.execute(
                "SELECT id,session_id,role,content,tool_calls,tool_call_id FROM messages ORDER BY id")]

    def session_rows(self):
        from hermes_security import sqlite
        path = self.home / "state.db"
        if not path.exists():
            return []
        with sqlite.connect(f"file:{path}?mode=ro", uri=True) as db:
            db.row_factory = sqlite.row_class()
            return [dict(row) for row in db.execute("SELECT id,parent_session_id FROM sessions")]

    def delegation_rows(self):
        from hermes_security import sqlite
        path = self.home / "state.db"
        if not path.exists():
            return []
        with sqlite.connect(f"file:{path}?mode=ro", uri=True) as db:
            db.row_factory = sqlite.row_class()
            return [dict(row) for row in db.execute(
                "SELECT delegation_id,parent_session_id,state,completed_at,result_json FROM async_delegations")]

    def capture_proof(self):
        import hashlib
        from hermes_security.io import read_json
        from hermes_cli.update_receipt import read_latest_receipt

        def select(record, names):
            return {name: record[name] for name in names if name in record}

        def result_view(result):
            view = select(result, ("status", "mode", "delegation_id", "subagent_ids", "session_id",
                                   "exit_code"))
            if result.get("command") in {UPDATE_COMMAND, GET_COMMAND, SET_COMMAND}:
                view["command"] = result["command"]
            if "output" in result:
                output = str(result["output"])
                view["output_sha256"] = hashlib.sha256(output.encode()).hexdigest()
                if output.strip() == "23":
                    view["config_value"] = "23"
            if isinstance(result.get("results"), list):
                view["results"] = [select(entry, ("task_index", "status")) for entry in result["results"]]
            return view

        calls = []
        for pair in paired_calls(self.messages()):
            calls.append({**select(pair, ("session_id", "message_id", "name")),
                "args": {**select(pair["args"], ("action", "session_id")),
                         **({"command": pair["args"]["command"]}
                            if pair["args"].get("command") in {UPDATE_COMMAND, GET_COMMAND, SET_COMMAND} else {})},
                "result": result_view(pair["result"])})
        delegations = []
        for row in self.delegation_rows():
            delegations.append({**select(row, ("delegation_id", "parent_session_id", "state", "completed_at")),
                                "result": result_view(json.loads(row["result_json"] or "{}"))})
        snapshot = {"baseline": self.args.baseline, "candidate": self.args.candidate,
            "source_head": self.head(), "parent_session_id": self.parent_id,
            "sessions": self.session_rows(), "calls": calls, "delegations": delegations,
            "child_identities": self.observer_identities(),
            "update_receipt": select(read_latest_receipt() or {},
                ("update_id", "outcome", "finished_at")),
            "processes": [select(read_json(path, purpose="state"),
                ("id", "parent_session_id", "exit_code"))
                for path in (self.home / "logs/process-results").glob("proc_*.json")]}
        encoded = json.dumps(snapshot, indent=2, sort_keys=True)
        for secret in (self.password, *self.secret_values):
            if secret:
                encoded = encoded.replace(secret, "<fixture-secret-redacted>")
        (self.evidence / "durable-tool-proof.json").write_text(encoded)
        self.receipt["durable_tool_proof_sha256"] = hashlib.sha256(encoded.encode()).hexdigest()

    def observer_identities(self):
        from scripts.e2e.active_update_observer import read_identities
        path = getattr(self, "observer_path", None)
        return read_identities(path) if path is not None else []

    def runtime_identity(self):
        import psutil
        from hermes_cli.update_lock import _is_runtime_host
        children = psutil.Process(self.terminal.process.pid).children(recursive=True)
        found = [process for process in children if _is_runtime_host(process.cmdline())]
        if len(found) != 1:
            raise RuntimeError("exactly one owned private native runtime must serve the public Ink process")
        return {"pid": found[0].pid, "created_at": found[0].create_time(),
                "python_executable": found[0].exe(), "argv": found[0].cmdline()}

    def wait_reply(self, tag, since, timeout=240):
        self.terminal.timeout = timeout
        def completed(_screen):
            if self.terminal.output.count(b"Unlock master password:") > 1:
                raise RuntimeError("a child or later parent turn asked for a second human unlock")
            return "─ ready │" in "\n".join(self.terminal.screen.display()) and any(row["id"] > since and row["role"] == "assistant" and not row["tool_calls"]
                       and (self.parent_id is None or row["session_id"] == self.parent_id)
                       and tag in (row["content"] or "") for row in self.messages())
        self.terminal.until(completed, "durable assistant completion: " + tag)

    def phase(self, prompt, tag, timeout=240):
        before = max((row["id"] for row in self.messages()), default=0)
        self.terminal.submit(prompt, rendered_suffix=prompt[-80:])
        self.wait_reply(tag, before, timeout)

    def run(self):
        from tests.tui_runtime.test_terminal_model_pty import TerminalPTY
        if self.args.baseline == self.args.candidate or self.head() != self.args.baseline:
            raise RuntimeError("the controlled native install must start at the exact distinct baseline")
        from scripts.e2e.active_update_observer import install_observer
        self.observer_path = install_observer(self.home, self.evidence)
        self.receipt["instrumentation"] = "fixture-owned subagent_start identity-only observer installed before session"
        before_ids = {row["id"] for row in self.session_rows()}
        self.terminal = TerminalPTY(self.env, self.password, self.evidence / "ink-transcript.log",
            argv=[str(self.args.launcher.resolve()), "chat", "--yolo", "--accept-hooks", "--toolsets", "terminal,file,delegation"],
            project=self.workspace, timeout=240, sensitive_values=self.secret_values)
        self.terminal.until(lambda text: "─ ready │" in text and "❯" in text,
                            "public Ink input after the initial vault gate")
        nonce = secrets.token_hex(16)
        self.phase("Retain this conversation-only marker for a later turn: " + nonce +
                   ". Reply exactly ACTIVE-READY; do not use tools.", "ACTIVE-READY")
        parents = [row["id"] for row in self.session_rows()
                   if row["id"] not in before_ids and row["parent_session_id"] is None]
        if len(parents) != 1:
            raise RuntimeError("the pre-update turn must create exactly one native parent session")
        parent = parents[0]
        self.parent_id = parent
        identity = self.runtime_identity()
        prefix = [row for row in self.messages() if row["session_id"] == parent]
        self.receipt.update(parent_session_id=parent, runtime_before=identity, durable_prefix_sha256=prefix_hash(prefix))
        self.phase("Use terminal to run this exact foreground command, timeout=600: `" + UPDATE_COMMAND +
                   "`. It updates THIS controlled install. Do not substitute --check, combine shell commands, "
                   "restart this session, or pass a password/key. Only after it succeeds, reply exactly ACTIVE-UPDATED.",
                   "ACTIVE-UPDATED", timeout=3000)
        if self.head() != self.args.candidate or self.runtime_identity() != identity:
            raise RuntimeError("source must advance to candidate without replacing the active native runtime")
        self.receipt["runtime_after_update"] = self.runtime_identity()
        marker = self.workspace / "post-update-delegated.txt"
        self.phase("Continue this SAME conversation. First run `" + SET_COMMAND + "` through terminal. "
                   "Then use delegate_task for ONE subagent to run `" + GET_COMMAND + "` through terminal and "
                   f"write exactly {nonce} to {marker}, verifying the file. Do not pass passwords/keys or restart. "
                   "Only once those actual calls finish, reply exactly ACTIVE-FOLLOWUP.", "ACTIVE-FOLLOWUP", timeout=900)
        self.phase("Recover the marker from our first turn, not files or configuration. Reply exactly "
                   "ACTIVE-HISTORY followed by that marker, without tools.", "ACTIVE-HISTORY " + nonce)
        self.verify(parent, identity, prefix, marker, nonce)
        self.terminal.submit("/quit")
        self.terminal.until(lambda _text: self.terminal.process.poll() is not None, "public Ink graceful exit")
        if self.terminal.process.returncode != 0:
            raise RuntimeError("the real public Ink session did not exit cleanly")
        probe = subprocess.run([str(self.args.launcher.resolve()), "config", "get", "agent.max_turns"],
            env=self.env, cwd=self.workspace, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60,
            start_new_session=True, check=False)
        if probe.returncode == 0 or "vault is locked and no terminal" not in (probe.stdout + probe.stderr).lower():
            raise RuntimeError("an independent closed-owner process did not refuse the locked vault")
        self.receipt.update(status="passed", closed_owner_refused=True)

    def verify(self, parent, identity, prefix, marker, nonce):
        from hermes_cli.config import read_raw_config
        from hermes_cli.update_receipt import read_latest_receipt
        rows = self.messages()
        if [row for row in rows if row["id"] in {item["id"] for item in prefix}] != prefix:
            raise RuntimeError("the original durable prompt prefix was changed by the active update")
        children = {row["id"] for row in self.session_rows() if row["parent_session_id"] == parent}
        from scripts.e2e.active_update_observer import read_identities
        calls = verify_calls(rows, parent, children, delegations=self.delegation_rows(),
                             child_links=read_identities(self.observer_path))
        receipt = read_latest_receipt() or {}
        if receipt.get("outcome") != "success" or not receipt.get("finished_at") or not receipt.get("post_update") or receipt.get("followups"):
            raise RuntimeError("the actual child updater did not publish a settled successful receipt")
        if self.runtime_identity() != identity or self.head() != self.args.candidate:
            raise RuntimeError("the same native runtime must own every later turn on the updated checkout")
        if not marker.is_file() or marker.read_text().strip() != nonce or read_raw_config()["agent"]["max_turns"] != 23:
            raise RuntimeError("post-update delegated artifact or actual configuration proof failed")
        if self.terminal.prompts != 1 or self.terminal.output.count(b"Unlock master password:") != 1:
            raise RuntimeError("active update must consume exactly one initial human unlock, no second prompt")
        if (self.home / "state.db").read_bytes()[:16] == b"SQLite format 3\0":
            raise RuntimeError("the actual session database is not encrypted")
        self.receipt.update(calls=calls, child_sessions=len(children), same_runtime=True,
            runtime_after_followup=self.runtime_identity(), original_prefix_unchanged=True,
            history_verified=True, passphrase_prompts=1, child_passphrase_prompts=0,
            encrypted_state_verified=True, update_id=receipt.get("update_id"), post_update_followups=[])

    def close(self):
        from tests.e2e.core.mcp_plugins._native_process import tagged_pids

        def owned():
            return {(pid, started): "owned active-update descendant" for pid in tagged_pids(self.tag)
                    if (started := self.reaper.start_time(pid)) is not None}

        before = owned()
        try:
            if self.terminal is not None:
                self.terminal.close()
        finally:
            forced = self.reaper.reap(lambda: {**before, **owned()}, timeout=10)
            self.reaper.release_orphans()
            self.receipt["cleanup_verified"] = not tagged_pids(self.tag)
            self.receipt["forced_cleanup_count"] = len(forced)
            self.receipt["terminal_process_reaped"] = bool(self.terminal and self.terminal.process.poll() is not None)
            if not self.receipt["cleanup_verified"] or forced:
                self.receipt["status"] = "failed"
            previous_status, self.receipt["status"] = self.receipt["status"], "failed"
            try:
                self.capture_proof()
                self.receipt["status"] = previous_status
            except Exception as error:
                self.receipt["proof_capture_error"] = type(error).__name__
                # Keep the original product/grade failure reason alongside evidence failure.
                if previous_status != "failed":
                    self.receipt["error"] = "DurableProofCaptureFailed"
                raise
            finally:
                self.vault.lock_now(self.home)
                (self.evidence / "receipt.json").write_text(json.dumps(self.receipt, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("root", "install-root", "home", "launcher", "evidence"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--kind", choices=("live", "strict-fixture"), required=True)
    args = parser.parse_args()
    acceptance = Acceptance(args, json.load(sys.stdin))
    try:
        acceptance.run()
    except BaseException as error:
        acceptance.receipt.update(status="failed", error=type(error).__name__)
        raise
    finally:
        acceptance.close()


if __name__ == "__main__":
    main()
