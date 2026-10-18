"""Run one bounded install/update scenario in a disposable Linux or macOS environment.

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
import shlex
import select
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import termios
import time
from urllib.parse import urlsplit

SCENARIOS = ("fresh", "update", "broken-files", "dirty-install", "reinstall", "upstream-install")
UPSTREAM_BASELINE = "eb75627faf532fa6ffbdae59a0d14b0b385fc780"
_PROMPT = re.compile(rb"(?:Create master password|Repeat master password|Master password):\s*", re.IGNORECASE)


def check_provider_transport(credential):
    """Reject unreachable gateway routes before provisioning a disposable installation."""
    if not credential.get("base_url"):
        return
    route = urlsplit(credential["base_url"])
    if route.scheme not in {"http", "https"} or not route.hostname:
        raise ValueError("the gateway base URL must have an HTTP(S) host")
    try:
        with socket.create_connection((route.hostname, route.port or (443 if route.scheme == "https" else 80)),
                                      timeout=15) as connection:
            if route.scheme == "https":
                with ssl.create_default_context().wrap_socket(connection, server_hostname=route.hostname):
                    return
    except OSError as exc:
        raise RuntimeError("Configured gateway is unreachable from this environment. Check DNS, TLS and "
                           "private-network connectivity before installation.") from exc


class Rehearsal:
    def __init__(self, args, credential):
        self.args, self.credential = args, credential
        self.root = Path(tempfile.mkdtemp(prefix="hermes-lifecycle-", dir=args.work_dir))
        self.home, self.code = self.root / "state", self.root / "state" / "hermes-agent"
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.evidence = args.evidence.resolve()
        self.evidence.mkdir(parents=True, exist_ok=True)
        if any(self.evidence.iterdir()):
            raise FileExistsError("choose an empty evidence directory for each attempt")
        print(f"[{args.scenario}] evidence: {self.evidence}", flush=True)
        self.password = secrets.token_urlsafe(32)
        self.resume_token = secrets.token_hex(24)
        self.legacy_nonce = secrets.token_hex(24)
        self.redactions = [self.password, *self._credential_strings(credential)]
        inherited = {"PATH", "USER", "LOGNAME", "SHELL", "SSL_CERT_FILE", "SSL_CERT_DIR",
                     "REQUESTS_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"}
        self.env = {key: value for key, value in os.environ.items()
                    if key in inherited or key.startswith("LC_")}
        self.env.update(HOME=str(self.root / "user"), HERMES_HOME=str(self.home),
                        HERMES_RUNTIME_DIR=str(args.tool_cache.resolve() if args.tool_cache else self.root / "runtime"),
                        GIT_CONFIG_GLOBAL=str(self.root / "gitconfig"), GIT_CONFIG_NOSYSTEM="1",
                        GIT_PAGER="cat",
                        TZ="UTC", LANG="C.UTF-8", TERM="dumb")
        Path(self.env["HOME"]).mkdir()
        self.env["PATH"] = str(Path(self.env["HOME"]) / ".local" / "bin") + os.pathsep + self.env["PATH"]
        self.receipt = {"scenario": args.scenario, "baseline": args.baseline,
                        "candidate": args.candidate, "live_provider": credential["provider"],
                        "model": credential["model"], "checks": {}, "status": "running",
                        "tool_cache_reused": args.tool_cache is not None}
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
            result = subprocess.run(argv, check=False, input=json.dumps(data), text=True, capture_output=True,
                                    env=self.env, cwd=cwd or self.workspace, timeout=timeout)
            text = self.clean(result.stdout + result.stderr)
            log.write_text(text)
            if result.returncode:
                raise RuntimeError(f"{name} failed ({result.returncode}); see {log.name}")
            return result.stdout
        master, slave = pty.openpty()
        proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave,
                                env=self.env, cwd=cwd or self.workspace, start_new_session=True,
                                preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))  # noqa: PLW1509 -- single-threaded controller; child needs its controlling PTY
        os.close(slave)
        output = bytearray()
        consumed = count = 0
        last_progress = started
        last_flush = started
        try:
            while True:
                if time.monotonic() - started > timeout:
                    raise TimeoutError(f"{name} exceeded {timeout}s; passphrase prompts={count}, expected={prompts}")
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
                    # Ink / PTY redraws can arrive a few bytes at a time. A
                    # full redacted snapshot per read turns logging quadratic;
                    # keep progress visible without saturating a slow VM disk.
                    if time.monotonic() - last_flush >= 1:
                        log.write_text(self.clean(output.decode(errors="replace")))
                        last_flush = time.monotonic()
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
                proc.wait(timeout=10)
            os.close(master)
            log.write_text(self.clean(output.decode(errors="replace")))
            self.receipt["checks"][name] = {"passphrase_prompts": count,
                                                   "seconds": round(time.monotonic() - started, 2)}
        return output.decode(errors="replace")

    def probe(self, action, *, timeout=60, **extra):
        # The probe is from the candidate harness, against each install's REAL imports.
        script = Path(__file__).with_name("state_probe.py").resolve()
        # A late application import may select/re-exec the PM generation. Finish
        # that boot BEFORE consuming stdin, just as the public launcher does.
        boot = "import hermes_bootstrap, runpy; runpy.run_path(" + repr(str(script)) + ", run_name='__main__')"
        text = self.run(action, [str(self.python), "-c", boot], cwd=self.code,
                        data={"action": action, "password": self.password, **extra}, timeout=timeout)
        return json.loads(text.strip().splitlines()[-1])

    def install(self, sha, *, name="install", prompts=0):
        self.run(name, ["bash", str(self.args.source / "scripts" / "install.sh"),
                             "--dir", str(self.code), "--hermes-home", str(self.home),
                             "--branch", "main", "--commit", sha,
                             "--non-interactive", "--skip-browser", "--skip-computer-use"], prompts=prompts, timeout=2400)
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
            "Then YOU run `hermes update --check` and `hermes config set agent.max_turns 23` through terminal. "
            "Use the existing unlocked session, never ask for or pass a passphrase/key. "
            "Run each of those three hermes commands as its own foreground terminal tool call "
            "with timeout=120; do not combine them with other commands or use execute_code. "
            "Do not simulate these actions; perform them, verify the file, and stop."
        )

        self.run("live-" + phase, [str(self.launcher), "chat", "--yolo", "--accept-hooks",
                                   "--toolsets", "terminal,file,delegation", "--max-turns", "10",
                                   "-q", prompt], prompts=1, timeout=660)
        if not marker.is_file() or marker.read_text().strip() != nonce:
            raise RuntimeError("live LLM/delegation did not produce the independent file oracle")
        self.receipt["checks"]["live-" + phase].update(self.probe("live-proof"))
        self.receipt["checks"]["live-" + phase]["artifact_verified"] = True

    def resume(self):
        marker = self.workspace / "restored-conversation.txt"
        prompt = ("Use the retained marker from this conversation's earlier messages. "
                  f"Write exactly that marker, without decoration, to {marker} using terminal. "
                  "Recover it from conversation history, not files or configuration. "
                  "Verify the file and stop.")

        self.run("resume-candidate", [str(self.launcher), "chat", "--yolo", "--accept-hooks",
                 "--resume", "e2e-retained", "--toolsets", "terminal,file", "--max-turns", "6", "-q", prompt],
                 prompts=1, timeout=420,
                 )
        if not marker.is_file() or marker.read_text().strip() != self.resume_token:
            raise RuntimeError("resumed LLM conversation did not recover its retained marker")
        self.receipt["checks"]["resume-candidate"].update(self.probe("resume-proof"))
        self.receipt["checks"]["resume-candidate"]["artifact_verified"] = True

    def reject_independent_cli(self):
        result = subprocess.run([str(self.launcher), "config", "get", "agent.max_turns"],
                                env=self.env, cwd=self.workspace, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=120, start_new_session=True, check=False)
        (self.evidence / "independent-locked-cli.log").write_text(self.clean(result.stdout + result.stderr))
        if result.returncode == 0:
            raise RuntimeError("an independent process inherited a closed session's unlock")
        if "vault is locked and no terminal" not in (result.stdout + result.stderr).casefold():
            raise RuntimeError("independent CLI failed for something other than the expected locked-vault gate")
        self.receipt["independent_locked_cli_rejected"] = True

    def provider_conversations(self):
        """Qualify every promised gateway dialect inside the same isolated OS environment."""
        from scripts.e2e.provider_matrix import CONNECTIONS

        self.run("enable-provider-dialects", [str(self.launcher), "pm", "install", "--extra", "anthropic"],
                 prompts=1, timeout=900)
        self.resolve_python()
        proofs = {}
        for mode in CONNECTIONS["litellm"].modes:
            evidence = self.workspace / ("provider-" + mode)
            self.run("conversation-" + mode, [str(self.python), "-m", "scripts.e2e.provider_conversation",
                     "--evidence", str(evidence)], cwd=self.code,
                     data={**self.credential, "api_mode": mode}, timeout=900)
            proofs[mode] = json.loads((evidence / "receipt.json").read_text())
        self.receipt["provider_conversations"] = proofs

    def prepare_transport(self):
        if self.args.transport_url:
            url = self.args.transport_url
        else:
            remote = self.root / "remote.git"
            self.run("prepare-transport", ["git", "clone", "--bare", "--shared", str(self.args.source), str(remote)])
            self.run("enable-filtered-clone", ["git", "--git-dir", str(remote), "config", "uploadpack.allowFilter", "true"])
            self.run("pin-candidate", ["git", "--git-dir", str(remote), "update-ref", "refs/heads/main", self.args.candidate])
            url = remote.as_uri()
        config = (f'[url "{url}"]\n\tinsteadOf = https://github.com/majorissuerep/hermes-agent.git\n'
                  f'\tinsteadOf = https://github.com/majorissuerep/hermes-agent\n'
                  f'\tinsteadOf = https://github.com/NousResearch/hermes-agent.git\n')
        Path(self.env["GIT_CONFIG_GLOBAL"]).write_text(config)

    def seed_legacy(self):
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "config.yaml").write_text("# preserved legacy install\nagent:\n  max_turns: 37\n")
        (self.home / "memories").mkdir(exist_ok=True)
        (self.home / "memories" / "legacy.md").write_text(self.legacy_nonce)
        (self.home / ".env").write_text("E2E_LEGACY_SECRET=" + self.legacy_nonce + "\n")
        if self.args.scenario == "dirty-install":
            old = Path(self.env["HOME"]) / ".local" / "bin" / "hermes"
            old.parent.mkdir(parents=True)
            old.write_text("#!/bin/sh\nexec " + shlex.quote(str(self.code / "venv/bin/python")) +
                           " -m hermes_cli.main \"$@\"\n")
            old.chmod(0o755)

    def prepare_install(self):
        scenario = self.args.scenario
        if scenario == "dirty-install":
            self.seed_legacy()
        initial = self.args.candidate if scenario in {"fresh", "dirty-install"} else self.args.baseline
        if scenario == "upstream-install":
            self.run("verify-upstream-ancestry", ["git", "-C", str(self.args.source), "merge-base",
                                                "--is-ancestor", UPSTREAM_BASELINE, self.args.candidate])
            self.install(UPSTREAM_BASELINE, name="install-upstream")
            self.seed_legacy()
            script = ("import hermes_bootstrap, json, sys; from hermes_state import SessionDB; "
                      "r=json.load(sys.stdin); db=SessionDB(); db.create_session('e2e-upstream', 'cli'); "
                      "db.append_message('e2e-upstream', 'user', r['nonce']); "
                      "db.append_message('e2e-upstream', 'assistant', 'retained upstream answer'); db.close(); "
                      "from hermes_cli.backup import create_quick_snapshot; "
                      "from hermes_cli.backup import run_backup; from argparse import Namespace; "
                      "from hermes_constants import get_hermes_home; "
                      "run_backup(Namespace(output=str(get_hermes_home()/'backups'/'upstream-retained.zip'))); "
                      "print(json.dumps({'snapshot': create_quick_snapshot(label='upstream-retained')}))")
            seeded = self.run("seed-upstream-session", [str(self.python), "-c", script],
                              cwd=self.code, data={"nonce": self.legacy_nonce})
            self.receipt["upstream_snapshot"] = json.loads(seeded.strip().splitlines()[-1])["snapshot"]
            if not self.receipt["upstream_snapshot"]:
                raise RuntimeError("unmodified upstream failed to create its recovery point")
            self.run("mark-upstream-origin", ["git", "-C", str(self.code), "remote", "set-url", "origin",
                                             "https://github.com/NousResearch/hermes-agent.git"])
            self.receipt["upstream_baseline"] = UPSTREAM_BASELINE
            initial = self.args.candidate
        self.install(initial)
        self.run("baseline-branch", ["git", "-C", str(self.code), "checkout", "-B", "main", initial])
        self.run("initialize-vault", [str(self.launcher), "secure-vault", "migrate", "--yes"], prompts=2)
        if scenario in {"dirty-install", "upstream-install"}:
            self.receipt["legacy_state_hashes"] = self.probe("legacy-proof", nonce=self.legacy_nonce, max_turns=37)
        if scenario == "upstream-install":
            self.receipt["upstream_session_hash"] = self.probe("upstream-proof", nonce=self.legacy_nonce)
            restored = self.probe("restore-upstream", snapshot=self.receipt["upstream_snapshot"],
                                  nonce=self.legacy_nonce, max_turns=37)
            if (restored["legacy"] != self.receipt["legacy_state_hashes"]
                    or restored["session"] != self.receipt["upstream_session_hash"]):
                raise RuntimeError("pre-encryption upstream recovery point changed retained state")
            self.receipt["upstream_restore_verified"] = True
            self.probe("mutate-legacy")
            self.run("import-upstream-backup", [str(self.launcher), "import",
                     str(self.home / "backups" / "upstream-retained.zip"), "--force"], prompts=1, timeout=180)
            if (self.probe("legacy-proof", nonce=self.legacy_nonce, max_turns=37) != self.receipt["legacy_state_hashes"]
                    or self.probe("upstream-proof", nonce=self.legacy_nonce) != self.receipt["upstream_session_hash"]):
                raise RuntimeError("pre-encryption upstream full backup changed retained state")
            self.receipt["upstream_backup_verified"] = True
        self.probe("seed", credential=self.credential, workspace=str(self.workspace), resume_token=self.resume_token)

    def advance_install(self):
        scenario = self.args.scenario
        if scenario in {"update", "broken-files", "reinstall"} and self.args.baseline == self.args.candidate:
            raise RuntimeError("update and repair must cross distinct commits")
        if scenario in {"update", "broken-files"}:
            if scenario == "broken-files":
                (self.code / "cli.py").write_text("# deliberately damaged tracked source\n")
                (self.code / "rehearsal-untracked.txt").write_text("retain local work")
            self.run("update", [str(self.launcher), "update", "--yes", "--keep-stash", "--backup"],
                     prompts=1, timeout=2400)
            self.resolve_python()
            self.receipt["update_status"] = self.probe("update-status")
            if scenario == "broken-files":
                retained = self.run("verify-local-work", ["git", "-C", str(self.code), "show",
                    "refs/stash^3:rehearsal-untracked.txt"]).strip()
                if retained != "retain local work":
                    raise RuntimeError("repair lost the unrelated local work requested in the stash")
        elif scenario == "reinstall":
            (self.code / "hermes_cli" / "main.py").unlink()
            (self.code / "reinstall-untracked.txt").write_text("retain reinstall work")
            if not self.python.is_relative_to(self.root):
                raise RuntimeError("refusing to damage a runtime outside the disposable installation")
            self.python.unlink()
            self.install(self.args.candidate, name="reinstall-source-and-runtime", prompts=1)
            retained = self.run("verify-reinstall-work", ["git", "-C", str(self.code), "show",
                "refs/stash^3:reinstall-untracked.txt"]).strip()
            if retained != "retain reinstall work":
                raise RuntimeError("installer repair lost unrelated local work")
            self.receipt["installer_repair_verified"] = True
        actual = self.run("verify-sha", ["git", "-C", str(self.code), "rev-parse", "HEAD"]).strip()
        if actual != self.args.candidate:
            raise RuntimeError("installed source did not reach the exact candidate commit")
        self.run("candidate-version", [str(self.launcher), "--version"])

    def recover_state(self, expected):
        snapshot = self.probe("snapshot")["snapshot"]
        if self.probe("restore", snapshot=snapshot) != expected:
            raise RuntimeError("snapshot restore changed semantic state")
        backup = self.workspace / "retained-backup.zip"
        self.run("full-backup", [str(self.launcher), "backup", "--output", str(backup)], prompts=1, timeout=180)
        self.probe("mutate-retained")
        if self.args.scenario == "reinstall":
            for name in ("config.yaml", ".env"):
                (self.home / name).unlink()
            self.install(self.args.candidate, name="reseed-missing-state", prompts=1)
            self.probe("seeded-state-proof")
            self.receipt["installer_state_reseed_verified"] = True
        self.run("import-full-backup", [str(self.launcher), "import", str(backup), "--force"], prompts=1, timeout=180)
        if self.probe("manifest") != expected:
            raise RuntimeError("encrypted full backup import changed semantic state")
        self.receipt["full_backup_verified"] = True
        self.probe("clobber-state")
        self.run("repair-encrypted-state", [str(self.launcher), "secure-vault", "repair"], prompts=1)
        if self.probe("manifest") != expected:
            raise RuntimeError("encrypted-state repair changed retained data")
        self.receipt["vault_repair_verified"] = True
        archive = self.workspace / "plaintext-recovery.tar"
        self.run("export-recovery", [str(self.launcher), "secure-vault", "export", "--output", str(archive)], prompts=1)
        proof = self.probe("export-proof", archive=str(archive), recovered=str(self.workspace / "recovered"), timeout=180)
        if proof != expected:
            raise RuntimeError("bulk decryption/re-encryption changed retained state")
        self.receipt["bulk_export_verified"] = True

    def execute(self):
        self.prepare_transport()
        self.prepare_install()
        before = self.probe("manifest")
        self.advance_install()
        self.receipt["host"] = self.probe("host-facts")
        after = self.probe("manifest")
        if after != before:
            raise RuntimeError("installation changed retained state: " + ", ".join(k for k in before if before[k] != after.get(k)))
        self.recover_state(after)
        self.live("candidate")
        self.resume()
        self.reject_independent_cli()
        if self.args.scenario == "fresh" and self.credential["provider"] == "litellm":
            self.provider_conversations()
        self.probe("manifest")
        if self.args.scenario in {"dirty-install", "upstream-install"}:
            self.probe("legacy-proof", nonce=self.legacy_nonce, max_turns=23)
            self.receipt["legacy_state_verified"] = True
        if self.args.scenario == "upstream-install":
            if self.probe("upstream-proof", nonce=self.legacy_nonce) != self.receipt["upstream_session_hash"]:
                raise RuntimeError("original upstream conversation changed after migration")
        self.receipt.update(status="passed", state_hashes=after, restore_verified=True)

    def close(self):
        passed = self.receipt.get("status") == "passed"
        cleanup_error = None
        try:
            if hasattr(self, "python") and (self.home / ".hermes-vault").is_file():
                self.probe("verify-runtime-closed")
                self.receipt["cleanup_verified"] = True
        except Exception as exc:  # health: allow BLE001 -- cleanup failure is recorded and fails an otherwise successful acceptance run
            cleanup_error = exc
            self.receipt["cleanup_error"] = self.clean(str(exc))
            if passed:
                self.receipt.update(status="failed", error="local runtime cleanup verification failed")
        (self.evidence / "receipt.json").write_text(json.dumps(self.receipt, indent=2) + "\n")
        if cleanup_error is not None:
            if passed:
                raise RuntimeError("local runtime cleanup verification failed; work retained for diagnosis") from cleanup_error
            return
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
    parser.add_argument("--tool-cache", type=Path, help="Optional PM tool-only cache for update/dirty rehearsals")
    parser.add_argument("--keep-work", action="store_true")
    parser.add_argument("--transport-url", help="Optional read-only test Git server whose main is the candidate")
    parser.add_argument("--credentials-stdin", action="store_true")
    args = parser.parse_args()
    if args.scenario == "fresh" and args.tool_cache:
        parser.error("fresh acceptance must provision its own cold tool store")
    inherited_credential = {key: os.environ.pop("E2E_LLM_" + key.upper(), "")
                            for key in ("provider", "model", "base_url", "api_key", "api_mode")}
    credential = json.load(sys.stdin) if args.credentials_stdin else inherited_credential
    credential["provider"] = credential.get("provider") or "litellm"
    if not credential.get("model") or not (credential.get("tokens") or
            (credential.get("base_url") and credential.get("api_key"))):
        parser.error("a real model credential is required; missing credentials never count as passed")
    check_provider_transport(credential)
    # The CLI accepts tags/abbreviations; evidence and the post-update check use
    # full object identities, including when a local operator passed a short SHA.
    ref_env = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "LANG"}}
    for field in ("baseline", "candidate"):
        resolved = subprocess.run(["git", "-C", str(args.source), "rev-parse", "--verify", "--end-of-options",
                                   getattr(args, field) + "^{commit}"], check=False,
                                  capture_output=True, text=True, env=ref_env, timeout=120)
        if resolved.returncode:
            parser.error(f"{field} is unavailable in the source checkout; fetch that ref first")
        setattr(args, field, resolved.stdout.strip())
    rehearsal = Rehearsal(args, credential)
    try:
        rehearsal.execute()
    except BaseException as exc:
        rehearsal.receipt.update(status="cancelled" if isinstance(exc, KeyboardInterrupt) else "failed",
                                 error=rehearsal.clean(str(exc)))
        if hasattr(rehearsal, "python") and (rehearsal.home / ".hermes-vault").is_file():
            try:
                rehearsal.probe("diagnostics")
            except Exception as diagnostic_error:  # health: allow BLE001 -- diagnostics must not replace the scenario failure; recorded in the receipt
                rehearsal.receipt["diagnostic_error"] = rehearsal.clean(str(diagnostic_error))
        raise
    finally:
        rehearsal.close()
    print(json.dumps({"scenario": args.scenario, "status": "passed", "candidate": args.candidate}))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    main()
