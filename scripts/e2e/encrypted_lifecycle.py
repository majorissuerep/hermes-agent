"""Run one bounded install/update scenario on a disposable Linux VM.

The real source installer, PM, public CLI, live LLM, delegation and encrypted
readers/writers run unchanged. A local bare Git transport pins OLD -> NEW without
publishing a fake version or touching a real user's home. Never run on live state.
"""
from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pty
import re
import secrets
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import termios
import time

SCENARIOS = ("fresh", "update", "broken-files", "dirty-install")
_PROMPT = re.compile(rb"(?:Create master password|Repeat master password|Master password):\s*")


class Rehearsal:
    def __init__(self, args, credential):
        self.args, self.credential = args, credential
        self.root = Path(tempfile.mkdtemp(prefix="hermes-lifecycle-", dir=args.work_dir))
        self.home, self.code = self.root / "state", self.root / "state" / "hermes-agent"
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.evidence = args.evidence.resolve()
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.password = secrets.token_urlsafe(32)
        self.redactions = [self.password, *self._credential_strings(credential)]
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("HERMES_", "__HERMES_", "E2E_LLM_"))}
        self.env.update(HOME=str(self.root / "user"), HERMES_HOME=str(self.home),
                        HERMES_RUNTIME_DIR=str(self.root / "runtime"),
                        GIT_CONFIG_GLOBAL=str(self.root / "gitconfig"), GIT_CONFIG_NOSYSTEM="1",
                        TZ="UTC", LANG="C.UTF-8", TERM="dumb")
        Path(self.env["HOME"]).mkdir()
        self.env["PATH"] = str(Path(self.env["HOME"]) / ".local" / "bin") + os.pathsep + self.env["PATH"]
        self.receipt = {"scenario": args.scenario, "baseline": args.baseline,
                        "candidate": args.candidate, "live_provider": credential["provider"],
                        "model": credential["model"], "checks": {}, "status": "running"}
        self.receipt["harness_sha256"] = hashlib.sha256(
            Path(__file__).read_bytes() + Path(__file__).with_name("state_probe.py").read_bytes()).hexdigest()

    @staticmethod
    def _credential_strings(value):
        if isinstance(value, dict):
            return [s for key, item in value.items() if key not in {"provider", "model", "base_url", "api_mode"}
                    for s in Rehearsal._credential_strings(item)]
        return [value] if isinstance(value, str) and len(value) > 8 else []

    def clean(self, value):
        for secret in self.redactions:
            value = value.replace(secret, "[REDACTED]")
        return value

    def run(self, name, argv, *, prompts=0, timeout=1800, data=None, cwd=None):
        print(f"[{self.args.scenario}] {name}", flush=True)
        started = time.monotonic()
        log = self.evidence / (name + ".log")
        if data is not None:
            result = subprocess.run(argv, input=json.dumps(data), text=True, capture_output=True,
                                    env=self.env, cwd=cwd or self.workspace, timeout=timeout)
            text = self.clean(result.stdout + result.stderr)
            log.write_text(text)
            if result.returncode:
                raise RuntimeError(f"{name} failed ({result.returncode}); see {log.name}")
            return result.stdout
        master, slave = pty.openpty()
        proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave,
                                env=self.env, cwd=cwd or self.workspace, start_new_session=True,
                                preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))
        os.close(slave)
        output = bytearray()
        consumed = count = 0
        last_progress = started
        try:
            while True:
                if time.monotonic() - started > timeout:
                    raise TimeoutError(f"{name} exceeded {timeout}s")
                if time.monotonic() - last_progress >= 30:
                    print(f"[{self.args.scenario}] {name}: still running", flush=True)
                    last_progress = time.monotonic()
                ready, _, _ = select.select([master], [], [], 1)
                if ready:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError as exc:
                        if exc.errno != errno.EIO:
                            raise
                        break
                    if not chunk:
                        break
                    output.extend(chunk)
                    if name == "install":
                        log.write_text(self.clean(output.decode(errors="replace")))
                    for match in _PROMPT.finditer(output, consumed):
                        count += 1
                        consumed = match.end()
                        if count > prompts:
                            raise RuntimeError(f"{name}: unexpected extra passphrase prompt ({count})")
                        os.write(master, self.password.encode() + b"\n")
                elif proc.poll() is not None:
                    break
            code = proc.wait(timeout=10)
            if code or count != prompts:
                raise RuntimeError(f"{name}: exit={code}, passphrase prompts={count}, expected={prompts}")
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            os.close(master)
            log.write_text(self.clean(output.decode(errors="replace")))
            self.receipt["checks"][name] = {"passphrase_prompts": count,
                                                   "seconds": round(time.monotonic() - started, 2)}
        return output.decode(errors="replace")

    def probe(self, action, **extra):
        # The probe is from the candidate harness, against each install's REAL imports.
        script = Path(__file__).with_name("state_probe.py").read_text()
        # A late application import may select/re-exec the PM generation. Finish
        # that boot BEFORE consuming stdin, just as the public launcher does.
        boot = "import hermes_bootstrap; exec(" + repr(script) + ")"
        text = self.run(action, [str(self.python), "-c", boot], cwd=self.code,
                        data={"action": action, "password": self.password, **extra})
        return json.loads(text.strip().splitlines()[-1])

    def install(self, sha):
        self.run("install", ["bash", str(self.args.source / "scripts" / "install.sh"),
                             "--dir", str(self.code), "--hermes-home", str(self.home),
                             "--branch", "main", "--commit", sha,
                             "--non-interactive", "--skip-browser", "--skip-computer-use"], timeout=2400)
        self.launcher = Path(self.env["HOME"]) / ".local" / "bin" / "hermes"
        if not self.launcher.is_file():
            raise RuntimeError("installer did not publish its launcher")
        self.resolve_python()

    def resolve_python(self):
        # Ask the same selector as the launcher, including legacy-install migration.
        script = ("from pathlib import Path; from pm.environments import selected_venv; "
                  "print(selected_venv(Path.cwd()) / 'bin' / 'python')")
        resolved = self.run("runtime-path", [sys.executable, "-c", script], cwd=self.code)
        self.python = Path(resolved.strip().splitlines()[-1])
        if not self.python.is_file():
            raise RuntimeError("the installed PM interpreter is missing")

    def live(self, phase):
        nonce = secrets.token_hex(12)
        marker = self.workspace / f"delegated-{phase}.txt"
        prompt = (
            f"This is an install acceptance task. Use delegate_task to ask ONE subagent to run "
            f"`hermes config get agent.max_turns` through terminal, then create the file {marker} "
            f"containing exactly {nonce}. The subagent must verify its contents. "
            "Then YOU run `hermes update --check` and `hermes config set agent.max_turns 17` through terminal. "
            "Use the existing unlocked session, never ask for or pass a passphrase/key. "
            "Do not simulate these actions; perform them, verify the file, and stop."
        )
        self.run("live-" + phase, [str(self.launcher), "chat", "--yolo", "--accept-hooks",
                                   "--toolsets", "terminal,file,delegation", "--max-turns", "10",
                                   "--run-budget", "240", "-q", prompt], prompts=1, timeout=300)
        if not marker.is_file() or marker.read_text().strip() != nonce:
            raise RuntimeError("live LLM/delegation did not produce the independent file oracle")
        self.receipt["checks"]["live-" + phase].update(self.probe("live-proof"))
        self.receipt["checks"]["live-" + phase]["artifact_verified"] = True

    def execute(self):
        remote = self.root / "remote.git"
        initial = self.args.candidate if self.args.scenario in {"fresh", "dirty-install"} else self.args.baseline
        if self.args.transport_url:
            url = self.args.transport_url
        else:
            self.run("prepare-transport", ["git", "clone", "--bare", "--shared", str(self.args.source), str(remote)])
            self.run("enable-filtered-clone", ["git", "--git-dir", str(remote), "config", "uploadpack.allowFilter", "true"])
            self.run("pin-candidate", ["git", "--git-dir", str(remote), "update-ref", "refs/heads/main", self.args.candidate])
            url = remote.as_uri()
        config = (f'[url "{url}"]\n\tinsteadOf = https://github.com/majorissuerep/hermes-agent.git\n'
                  f'\tinsteadOf = https://github.com/majorissuerep/hermes-agent\n')
        Path(self.env["GIT_CONFIG_GLOBAL"]).write_text(config)
        if self.args.scenario == "dirty-install":
            self.home.mkdir(parents=True)
            defaults = (self.args.source / "hermes_cli" / "config_defaults.py").read_text()
            version = re.search(r'["\']_config_version["\']:\s*(\d+)', defaults)
            if version is None:
                raise RuntimeError("candidate config schema is missing")
            (self.home / "config.yaml").write_text(
                f"# preserved legacy install\n_config_version: {version[1]}\ndisplay:\n  skin: default\n")
            (self.home / "memories").mkdir()
            (self.home / "memories" / "legacy.md").write_text("preserve previous install")
            old = Path(self.env["HOME"]) / ".local" / "bin" / "hermes"
            old.parent.mkdir(parents=True)
            old.write_text("#!/bin/sh\nexec /removed/old/hermes/venv/bin/python \"$@\"\n")
            old.chmod(0o755)
        self.install(initial)
        self.run("baseline-branch", ["git", "-C", str(self.code), "checkout", "-B", "main", initial])
        self.run("initialize-vault", [str(self.launcher), "secure-vault", "migrate", "--yes"], prompts=2)
        self.probe("seed", credential=self.credential, workspace=str(self.workspace))
        # OLD may lack session self-management; the live delegation contract is NEW's gate.
        before = self.probe("manifest")
        if self.args.scenario in {"update", "broken-files"}:
            if initial == self.args.candidate:
                raise RuntimeError("an update must cross two distinct commits")
            if self.args.scenario == "broken-files":
                (self.code / "cli.py").write_text("# deliberately damaged tracked source\n")
                (self.code / "rehearsal-untracked.txt").write_text("retain local work")
            self.run("update", [str(self.launcher), "update", "--yes", "--keep-stash", "--backup"], prompts=1, timeout=2400)
            actual = self.run("verify-sha", ["git", "-C", str(self.code), "rev-parse", "HEAD"]).strip()
            if actual != self.args.candidate:
                raise RuntimeError("updater did not reach the exact candidate commit")
            self.resolve_python()
            self.receipt["update_status"] = self.probe("update-status")
            if self.args.scenario == "broken-files" and "AIAgent" not in (self.code / "cli.py").read_text():
                raise RuntimeError("damaged source file was not repaired")
        after = self.probe("manifest")
        if after != before:
            raise RuntimeError("update changed retained state: " + ", ".join(k for k in before if before[k] != after.get(k)))
        snapshot = self.probe("snapshot")["snapshot"]
        restored = self.probe("restore", snapshot=snapshot)
        if restored != after:
            raise RuntimeError("snapshot restore did not preserve semantic state")
        self.live("candidate")
        self.probe("manifest")
        if self.args.scenario == "dirty-install" and not (self.home / "memories" / "legacy.md").exists():
            raise RuntimeError("dirty install discarded previous state")
        self.receipt.update(status="passed", state_hashes=after, restore_verified=True)

    def close(self):
        (self.evidence / "receipt.json").write_text(json.dumps(self.receipt, indent=2) + "\n")
        if not self.args.keep_work:
            shutil.rmtree(self.root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=SCENARIOS, required=True)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--keep-work", action="store_true")
    parser.add_argument("--transport-url", help="Optional read-only test Git server whose main is the candidate")
    parser.add_argument("--credentials-stdin", action="store_true")
    args = parser.parse_args()
    if args.credentials_stdin:
        credential = json.load(sys.stdin)
    else:
        credential = {key: os.environ.pop("E2E_LLM_" + key.upper(), "")
                      for key in ("provider", "model", "base_url", "api_key", "api_mode")}
        credential["provider"] = credential["provider"] or "litellm"
    if not credential.get("model") or not (credential.get("tokens") or
            (credential.get("base_url") and credential.get("api_key"))):
        parser.error("a real model credential is required; missing credentials never count as passed")
    rehearsal = Rehearsal(args, credential)
    try:
        rehearsal.execute()
        print(json.dumps({"scenario": args.scenario, "status": "passed", "candidate": args.candidate}))
    except BaseException as exc:
        rehearsal.receipt.update(status="cancelled" if isinstance(exc, KeyboardInterrupt) else "failed",
                                 error=rehearsal.clean(str(exc)))
        raise
    finally:
        rehearsal.close()


if __name__ == "__main__":
    main()
