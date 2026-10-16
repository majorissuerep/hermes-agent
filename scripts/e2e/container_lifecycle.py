"""Cold encrypted lifecycle acceptance in disposable Docker containers or Kubernetes pods.

The host only exports committed Git objects and orchestrates containers. All Hermes
installation, state, passphrases, model calls and recovery stay inside the container.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

SCENARIOS = ("fresh", "update", "broken-files", "dirty-install", "reinstall", "upstream-install")
IMAGE = "docker.io/library/ubuntu:24.04@sha256:f610ab94648195aa356059f5b41d6085c9d4d903c072430cdd1af7bdb646106b"
SNAPSHOT = "20261001T000000Z"
CA_PACKAGE_URL = ("https://snapshot.ubuntu.com/ubuntu/20261001T000000Z/pool/main/c/ca-certificates/"
                  "ca-certificates_20260601~24.04.1_all.deb")
CA_PACKAGE_SHA256 = "6bac2a01979e210d9eac1d4d56747ec709ea60654744d66705dc3c36e7629e50"


def command(argv, *, stdin=None, data=None, timeout=120):
    return subprocess.run(argv, input=data, stdin=stdin, capture_output=True,
                          timeout=timeout, check=True).stdout


def export_source(source: Path, baseline: str, candidate: str, output: Path) -> dict:
    """Export no worktree, host configuration, alternates, user homes or credentials."""
    def git(*args):
        return command(["git", "-C", str(source), *args]).decode().strip()

    refs = {key: git("rev-parse", "--verify", "--end-of-options", value + "^{commit}")
            for key, value in (("baseline", baseline), ("candidate", candidate))}
    if refs["baseline"] == refs["candidate"]:
        raise ValueError("baseline and candidate must be distinct commits")
    with tempfile.TemporaryDirectory(prefix="hermes-source-") as directory:
        root = Path(directory)
        bare = root / "source.git"
        command(["git", "init", "--bare", str(bare)])
        command(["git", "-c", "pack.threads=2", "-c", "pack.windowMemory=64m",
                 "--git-dir", str(bare), "fetch", "--no-tags",
                 source.resolve().as_uri(), refs["candidate"] + ":refs/heads/main",
                 refs["baseline"] + ":refs/heads/baseline"], timeout=600)
        command(["git", "--git-dir", str(bare), "symbolic-ref", "HEAD", "refs/heads/main"])
        command(["git", "--git-dir", str(bare), "merge-base", "--is-ancestor",
                 refs["baseline"], refs["candidate"]])
        command(["git", "--git-dir", str(bare), "fsck", "--full"], timeout=600)
        if (bare / "objects/info/alternates").exists():
            raise RuntimeError("source export must not depend on a host object store")
        bootstrap = command(["git", "-C", str(source), "show",
                             refs["candidate"] + ":scripts/e2e/container_bootstrap.sh"])
        harness = b"".join(command(["git", "-C", str(source), "show", refs["candidate"] + ":scripts/e2e/" + name])
                           for name in ("encrypted_lifecycle.py", "state_probe.py"))
        orchestrator = command(["git", "-C", str(source), "show",
                                refs["candidate"] + ":scripts/e2e/container_lifecycle.py"])
        (root / "bootstrap.sh").write_bytes(bootstrap)
        provenance = {**refs, "image": IMAGE, "ubuntu_snapshot": SNAPSHOT,
                      "bootstrap_sha256": hashlib.sha256(bootstrap).hexdigest(),
                      "harness_sha256": hashlib.sha256(harness).hexdigest(),
                      "orchestrator_sha256": hashlib.sha256(orchestrator).hexdigest()}
        (root / "source.json").write_text(json.dumps(provenance, sort_keys=True) + "\n")
        with tarfile.open(output, "w") as archive:
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                info = archive.gettarinfo(str(path), arcname=path.relative_to(root).as_posix())
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ""
                with path.open("rb") as stream:
                    archive.addfile(info, stream)
    with output.open("rb") as stream:
        provenance["source_archive_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    return provenance


def verify_receipt(receipt: dict, scenario: str, provenance: dict) -> None:
    """A green exit, claimed success, or partial matrix is never sufficient proof."""
    if (receipt.get("status") != "passed" or receipt.get("scenario") != scenario
            or any(receipt.get(key) != provenance[key] for key in ("baseline", "candidate", "harness_sha256"))
            or receipt.get("tool_cache_reused") is not False
            or receipt.get("restore_verified") is not True or receipt.get("cleanup_verified") is not True
            or receipt.get("vault_repair_verified") is not True or receipt.get("bulk_export_verified") is not True
            or receipt.get("full_backup_verified") is not True):
        raise ValueError("lifecycle receipt is incomplete, failed, cached or for another revision")
    live = receipt.get("checks", {}).get("live-candidate", {})
    if (live.get("passphrase_prompts") != 1 or live.get("child_sessions", 0) < 1
            or live.get("verified_hermes_calls") != 3 or not live.get("complete")
            or not live.get("artifact_verified")
            or not live.get("executed_in_child", {}).get("hermes config get agent.max_turns")):
        raise ValueError("live delegation and no-extra-unlock proof is incomplete")
    if scenario in {"update", "broken-files"}:
        if (receipt.get("checks", {}).get("update", {}).get("passphrase_prompts") != 1
                or receipt.get("update_status", {}).get("outcome") != "success"
                or receipt.get("update_status", {}).get("followups") != []):
            raise ValueError("the real update must finish with exactly one unlock")
    resumed = receipt.get("checks", {}).get("resume-candidate", {})
    if (resumed.get("passphrase_prompts") != 1 or not resumed.get("history_verified")
            or not resumed.get("artifact_verified") or receipt.get("independent_locked_cli_rejected") is not True):
        raise ValueError("resumed history and closed-session isolation were not proven")
    if scenario in {"dirty-install", "upstream-install"} and (receipt.get("legacy_state_verified") is not True
                                       or not receipt.get("legacy_state_hashes")):
        raise ValueError("dirty install must preserve pre-install config, memory and credentials")
    if scenario == "reinstall" and receipt.get("installer_repair_verified") is not True:
        raise ValueError("installer must repair damaged source and a missing runtime")
    if scenario == "upstream-install" and (not receipt.get("upstream_baseline")
            or not receipt.get("upstream_session_hash") or receipt.get("upstream_restore_verified") is not True
            or receipt.get("upstream_backup_verified") is not True):
        raise ValueError("upstream migration must preserve its original conversation and recovery point")


class Container:
    def __init__(self, name: str, namespace: str | None):
        self.name, self.namespace = name, namespace

    def shell(self, argv, **kwargs):
        return command([*self.exec_prefix(), *argv], **kwargs)

    def put(self, archive: Path, target="/inputs/source.tar"):
        with archive.open("rb") as stream:
            self.shell(["sh", "-c", 'umask 077; mkdir -p /inputs; cat > "$1"', "sh", target],
                       stdin=stream, timeout=600)

    def evidence(self, output: Path):
        raw = self.shell(["tar", "-C", "/rehearsal/evidence", "-cf", "-", "."], timeout=120)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            for member in archive.getmembers():
                if member.isdir():
                    continue
                path = Path(member.name)
                if (not member.isfile() or path.is_absolute() or ".." in path.parts
                        or len(path.parts) != 1 or path.suffix not in {".json", ".log"}):
                    raise ValueError("container attempted to export something other than sanitized evidence")
                with archive.extractfile(member) as stream:
                    (output / path.name).write_bytes(stream.read())


class Docker(Container):
    def start(self):
        command(["docker", "run", "-d", "--name", self.name, "--platform", "linux/amd64",
                 "--label", "hermes.encrypted-e2e=true", "--cpus=4", "--memory=8g",
                 "--pids-limit=2048", "--cap-drop=NET_RAW", "--security-opt=no-new-privileges",
                 IMAGE, "sleep", "infinity"], timeout=600)

    def exec_prefix(self):
        return ["docker", "exec", "-i", self.name]

    def identity(self):
        value = json.loads(command(["docker", "inspect", self.name]))[0]
        return {"container_id": value["Id"], "image_id": value["Image"], "backend": "docker"}

    def close(self):
        try:
            command(["docker", "rm", "-f", self.name])
        except subprocess.CalledProcessError as exc:
            if b"No such container:" not in (exc.stderr or b""):
                raise


class Kubernetes(Container):
    def kubectl(self):
        return ["kubectl", "--namespace", self.namespace]

    def start(self):
        pod = {"apiVersion": "v1", "kind": "Pod", "metadata": {
            "name": self.name, "namespace": self.namespace, "labels": {
                "app": "hermes-encrypted-e2e", "app.kubernetes.io/name": "hermes-encrypted-e2e",
                "app.kubernetes.io/part-of": "hermes-agent", "app.kubernetes.io/managed-by": "hermes-e2e"}},
            "spec": {"restartPolicy": "Never", "activeDeadlineSeconds": 5400,
                     "automountServiceAccountToken": False, "nodeSelector": {"kubernetes.io/arch": "amd64"},
                     "securityContext": {"seccompProfile": {"type": "RuntimeDefault"}},
                     "containers": [{"name": "lifecycle", "image": IMAGE, "imagePullPolicy": "IfNotPresent",
                        "command": ["sleep", "infinity"],
                        "securityContext": {"allowPrivilegeEscalation": False, "privileged": False,
                                            "capabilities": {"drop": ["NET_RAW"]}},
                        "resources": {"requests": {"cpu": "2", "memory": "4Gi", "ephemeral-storage": "10Gi"},
                                      "limits": {"cpu": "4", "memory": "8Gi", "ephemeral-storage": "40Gi"}}}]}}
        command([*self.kubectl(), "create", "-f", "-"], data=json.dumps(pod).encode())
        command([*self.kubectl(), "wait", "--for=condition=Ready", "pod/" + self.name,
                 "--timeout=300s"], timeout=310)

    def exec_prefix(self):
        return [*self.kubectl(), "exec", "-i", self.name, "--"]

    def identity(self):
        value = json.loads(command([*self.kubectl(), "get", "pod", self.name, "-o", "json"]))
        return {"pod_uid": value["metadata"]["uid"], "image_id": value["status"]["containerStatuses"][0]["imageID"],
                "backend": "kubernetes"}

    def close(self):
        command([*self.kubectl(), "delete", "pod", self.name, "--ignore-not-found", "--wait=true",
                 "--timeout=120s"], timeout=130)


def run_scenario(args, scenario, archive, ca_package, provenance, credential):
    output = args.evidence / scenario
    output.mkdir()
    container = {"docker": Docker, "kubernetes": Kubernetes}[args.backend](
        "hermes-e2e-" + secrets.token_hex(4) + "-" + scenario, args.namespace)
    isolation = {**provenance, "scenario": scenario, "status": "running", "cleanup_verified": False}
    started = False
    try:
        print(f"[{scenario}] creating cold {args.backend} container", flush=True)
        started = True
        container.start()
        isolation.update(container.identity())
        container.put(archive)
        actual = container.shell(["sha256sum", "/inputs/source.tar"]).decode().split()[0]
        if actual != provenance["source_archive_sha256"]:
            raise ValueError("source archive changed during container transport")
        container.shell(["tar", "-xf", "/inputs/source.tar", "-C", "/inputs"])
        container.put(ca_package, "/inputs/bootstrap-ca.deb")
        # Bootstrap has no LLM credentials; only the final driver receives them over stdin.
        bootstrap = container.shell(["bash", "/inputs/bootstrap.sh", CA_PACKAGE_SHA256], timeout=900)
        (output / "bootstrap.log").write_bytes(bootstrap)
        argv = ["runuser", "-u", "rehearsal", "--", "python3", "-u",
                "/source/scripts/e2e/encrypted_lifecycle.py", "--scenario", scenario,
                "--source", "/source", "--baseline", provenance["baseline"],
                "--candidate", provenance["candidate"], "--work-dir", "/rehearsal/work",
                "--evidence", "/rehearsal/evidence", "--credentials-stdin"]
        print(f"[{scenario}] real cold installer/update/restore/live-LLM rehearsal", flush=True)
        try:
            result = container.shell(argv, data=json.dumps(credential).encode(), timeout=4800)
            (output / "controller.log").write_bytes(result)
        finally:
            container.shell(["cp", "/rehearsal/os-packages.log", "/rehearsal/evidence/os-packages.log"])
            container.evidence(output)
        verify_receipt(json.loads((output / "receipt.json").read_text()), scenario, provenance)
        isolation["status"] = "passed"
    except Exception as exc:  # health: allow BLE001 -- every failure is recorded and still fails the matrix
        isolation.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        if isinstance(exc, subprocess.CalledProcessError):
            text = (exc.stdout or b"") + (exc.stderr or b"")
            for value in credential.values():
                if isinstance(value, str) and len(value) > 8:
                    text = text.replace(value.encode(), b"[REDACTED]")
            (output / "container-error.log").write_bytes(text)
        raise
    finally:
        try:
            if started:
                container.close()
            isolation["cleanup_verified"] = True
        except Exception as exc:  # health: allow BLE001 -- failed cleanup must never publish green acceptance
            isolation.update(status="failed", cleanup_error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            (output / "isolation.json").write_text(json.dumps(isolation, indent=2) + "\n")
    print(f"[{scenario}] passed; disposable container deleted", flush=True)
    return isolation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("docker", "kubernetes"), default="docker")
    parser.add_argument("--scenario", choices=("all", *SCENARIOS), default="all")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--namespace", help="Existing dedicated test namespace (Kubernetes only)")
    parser.add_argument("--max-parallel", type=int, choices=(1, 2), default=2)
    parser.add_argument("--credentials-stdin", action="store_true")
    args = parser.parse_args()
    inherited_credential = {
        key: os.environ.pop("E2E_LLM_" + key.upper(), "")
        for key in ("provider", "model", "base_url", "api_key", "api_mode")}
    credential = json.load(sys.stdin) if args.credentials_stdin else inherited_credential
    credential["provider"] = credential.get("provider") or "litellm"
    if not all(credential.get(key) for key in ("model", "base_url", "api_key")):
        parser.error("dedicated live-model credentials are required; missing credentials never pass")
    args.evidence = args.evidence.resolve()
    args.evidence.mkdir(parents=True, exist_ok=True)
    if any(args.evidence.iterdir()):
        parser.error("choose an empty evidence directory")
    owned_namespace = args.backend == "kubernetes" and not args.namespace
    gate = {"status": "failed", "failures": [], "scenarios": [], "cleanup_verified": False}
    namespace_created = False
    try:
        if owned_namespace:
            args.namespace = "hermes-e2e-" + secrets.token_hex(5)
            command(["kubectl", "create", "namespace", args.namespace])
            namespace_created = True
        with tempfile.TemporaryDirectory(prefix="hermes-container-source-") as directory:
            archive = Path(directory) / "source.tar"
            print("[source] exporting committed Git objects and verifying complete history", flush=True)
            provenance = export_source(args.source, args.baseline, args.candidate, archive)
            if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != provenance["orchestrator_sha256"]:
                raise ValueError("run the container orchestrator from the exact committed candidate")
            ca_package = Path(directory) / "bootstrap-ca.deb"
            with urllib.request.urlopen(CA_PACKAGE_URL, timeout=60) as response:
                ca_package.write_bytes(response.read())
            if hashlib.sha256(ca_package.read_bytes()).hexdigest() != CA_PACKAGE_SHA256:
                raise ValueError("bootstrap CA package differs from its pinned checksum")
            provenance.update(ca_package_url=CA_PACKAGE_URL, ca_package_sha256=CA_PACKAGE_SHA256)
            print("[source] source/archive and bootstrap certificate checksums verified", flush=True)
            scenarios = SCENARIOS if args.scenario == "all" else (args.scenario,)
            with ThreadPoolExecutor(max_workers=args.max_parallel) as pool:
                futures = [pool.submit(run_scenario, args, scenario, archive, ca_package, provenance, credential)
                           for scenario in scenarios]
                # Inspect every result; an early failure must not hide another running case.
                results, failures = [], []
                for future in futures:
                    try:
                        results.append(future.result())
                    except Exception as exc:  # health: allow BLE001 -- aggregate all legs without fail-fast cancellation
                        failures.append(f"{type(exc).__name__}: {exc}")
            gate.update(**provenance, status="failed" if failures else "passed",
                        expected_scenarios=list(scenarios), scenarios=results, failures=failures)
    except Exception as exc:  # health: allow BLE001 -- source/provision failures need evidence too
        gate["failures"].append(f"{type(exc).__name__}: {exc}")
    finally:
        try:
            if namespace_created:
                command(["kubectl", "delete", "namespace", args.namespace, "--wait=true", "--timeout=120s"], timeout=130)
            gate["cleanup_verified"] = True
        except Exception as exc:  # health: allow BLE001 -- namespace cleanup is part of acceptance
            gate["failures"].append(f"namespace cleanup failed: {type(exc).__name__}: {exc}")
        if gate["failures"]:
            gate["status"] = "failed"
        (args.evidence / "acceptance.json").write_text(json.dumps(gate, indent=2) + "\n")
    if gate["status"] != "passed":
        raise RuntimeError("container acceptance failed; inspect sanitized evidence")


if __name__ == "__main__":
    main()
