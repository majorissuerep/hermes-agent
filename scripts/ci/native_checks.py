"""Native host and daemon evidence that the ordinary in-house pod cannot supply."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
FORMAL_FILES = [
    "tests/e2e/core/formal_logic/test_native_checkpoints.py",
    "tests/e2e/core/formal_logic/test_terminal_mode.py",
]
LINUX_FILES = [
    "tests/hermes_platform/test_remote_workspace_file_read.py",
    "tests/tools/test_browser_client_private_stdio.py",
    "tests/tools/test_browser_recording_pending.py",
    "tests/hermes_security/test_session_custody.py",
    "tests/tools/test_browser_native_private_state.py",
    "tests/tools/test_browser_native_restore.py",
    "tests/tools/test_browser_native_custody_view.py",
    "tests/tools/test_browser_use_private_state.py",
    "tests/tools/test_browser_vendor_authority.py",
    "tests/hermes_cli/test_browser_use_import.py",
    "tests/hermes_cli/test_browser_use_profile_storage.py",
    "tests/hermes_cli/test_browser_worker_external.py",
    "tests/hermes_cli/test_browser_worker_store.py",
    "tests/hermes_cli/test_browser_worker_retirement.py",
    "tests/hermes_cli/test_browser_worker_retirement_survivors.py",
    "tests/hermes_cli/test_browser_worker_management.py",
    "tests/hermes_platform/test_client_capture.py",
    "tests/hermes_platform/test_browser_process.py",
    "tests/hermes_platform/test_remote_workspace.py",
    "tests/tools/test_browser_terminal.py",
    "tests/hermes_cli/test_browser_profile_store.py",
    "tests/hermes_cli/test_browser_profile_repair.py",
    "tests/hermes_platform/test_private_ipc_directory.py",
    "tests/tui_runtime/test_terminal_model_pty.py",
    "tests/tui_runtime/test_terminal_artifact_preview_pty.py",
    "tests/tui_runtime/test_terminal_global_model_pty.py",
    "tests/tui_runtime/test_terminal_session_naming_pty.py",
    "tests/tui_runtime/test_terminal_pty_custody.py",
    "tests/hermes_cli/test_update_finish.py",
    "tests/hermes_cli/test_key_only_startup_admission.py",
    "tests/tools/test_terminal_management_launcher.py",
    "tests/e2e/test_fixture_download_cache.py",
]


def run(*args: str, env: dict[str, str]) -> None:
    print("+ " + " ".join(args), flush=True)
    subprocess.run(args, cwd=ROOT, env=env, check=True, timeout=1800)


def bootstrap_protocol(env: dict[str, str], directory: Path) -> None:
    """Exercise the current installer/stamp protocol against this exact checkout."""
    manifest = subprocess.run(["bash", "-s", "--", "--manifest"],
                              input=(ROOT / "scripts/install.sh").read_bytes(),
                              capture_output=True, env=env, check=True, timeout=30)
    stages = json.loads(manifest.stdout)
    assert stages["protocol_version"] == 1
    assert [stage["name"] for stage in stages["stages"]] == [
        "prerequisites", "repository", "venv", "python-deps", "config", "products", "setup", "complete"]
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=30).strip()
    mirror, install = directory / "source.git", directory / "installed"
    run("git", "init", "--bare", str(mirror), env=env)
    run("git", "push", str(mirror), "HEAD:refs/heads/ci-under-test", env=env)
    installer_env = dict(env, HERMES_INSTALL_DIR=str(install), HERMES_HOME=str(directory / "home"),
                         HERMES_RUNTIME_DIR=str(directory / "tools"), HERMES_REPO_URL=str(mirror))
    for stage in ("prerequisites", "repository", "config", "complete"):
        run("bash", "scripts/install.sh", "--branch", "ci-under-test", "--commit", sha,
            "--non-interactive", "--stage", stage, "--json", env=installer_env)
    run(sys.executable, "scripts/verify-bootstrap-version-stamp.py", "--stamp",
        str(install / ".hermes-bootstrap-complete"), "--repo", str(install), "--expect-commit", sha,
        "--expect-branch", "ci-under-test", "--no-source-stamp", env=env)
    observed = subprocess.check_output(["git", "-C", str(install), "rev-parse", "HEAD"],
                                       text=True, timeout=30).strip()
    assert observed == sha


def formal_checks(env: dict[str, str]) -> None:
    """Forward the owning PM store explicitly across the hermetic test boundary."""
    from pm.paths import store_root

    run(sys.executable, "scripts/ci/install_lean.py", env=env)
    run("scripts/run_tests.sh", "--fail-on-skip", "--file-retries", "0", *FORMAL_FILES,
        "--", "--formal-checker-store", str(store_root().resolve()), env=env)


def main() -> None:
    if sys.platform.startswith("linux"):
        from scripts.ci.native_namespace_policy import reexec_if_required

        status = reexec_if_required()
        if status is not None:
            raise SystemExit(status)
    env = dict(os.environ, HERMES_TEST_FILE_RETRIES="0", HERMES_TEST_FILE_TIMEOUT="600",
               HERMES_TEST_WORKERS="2", HERMES_E2E_REQUIRE_TUI="1")
    for key in ("HERMES_TEST_PATHS", "HERMES_TEST_SLICE"):
        env.pop(key, None)
    if sys.platform == "darwin":
        files = subprocess.check_output([sys.executable, "scripts/ci/list_os_marked_tests.py", "macos"],
                                        cwd=ROOT, text=True, timeout=30).splitlines()
    elif sys.platform.startswith("linux"):
        files = LINUX_FILES
    else:
        raise SystemExit("Native qualification requires a supported Linux or macOS host")
    # macOS discovers every platform-marked file, including formal E2E cases.
    # Their required compiler is installed by the dedicated fail-on-skip phase.
    files = [path for path in files if path not in FORMAL_FILES]
    if not files:
        raise SystemExit("Native qualification must select tests")
    run("scripts/run_tests.sh", "--files", os.pathsep.join(files), "--file-retries", "0",
        "--", "-m", "platforms and not integration", env=env)
    if sys.platform.startswith("linux"):
        run(sys.executable, "-m", "scripts.ci.protected_management_checks", env=env)
        run("scripts/run_tests.sh", "--fail-on-skip", "--file-retries", "0",
            "tests/hermes_security/test_pipe_readonly_namespace.py",
            "tests/tools/test_ssh_environment.py", "tests/tools/test_docker_backend_real.py",
            "tests/scripts/e2e/test_container_docker_live.py", "tests/scripts/test_fresh_source_install.py", env=env)
        with tempfile.TemporaryDirectory(prefix="hermes-bootstrap-protocol-") as temporary:
            bootstrap_protocol(env, Path(temporary))
    # The opt-in verifier needs real kernel evidence on both supported native hosts.
    formal_checks(env)
    run("git", "diff", "--exit-code", env=env)
    if subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard"],
                               cwd=ROOT, text=True, timeout=30).strip():
        raise SystemExit("Native qualification left an untracked artifact")


if __name__ == "__main__":
    main()
