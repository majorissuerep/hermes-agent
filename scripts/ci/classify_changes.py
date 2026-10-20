#!/usr/bin/env python3
"""Fail-open changed-path selection for terminal, Python, docs and packaging CI.

The secret-free native lanes run protocol and coordinator contracts. Dedicated
live terminal lifecycle acceptance is an explicit immutable-commit workflow.
Unknown paths, empty input and workflow changes never silently skip Python.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

_FRONTEND = ("ui-tui/", "apps/shared/")  # TS typecheck-matrix packages
_FRONTEND_FILES = set()
_ROOT_NPM = {"package.json", "package-lock.json"}  # shifts every package's tree
_DOCKER_META = ("docker/", ".hadolint.yaml", "Dockerfile", ".dockerignore") # docker setup
_NIX_PATHS = ("nix/",) # nix files
_NIX_FILES = {"flake.nix", "flake.lock"} # base nix files
_SITE = ("website/", "skills/", "optional-skills/")  # docs site + skill pages
# Prose/frontend trees that can't touch Python. skills/ is excluded on purpose.
_PY_SKIP = ("docs/", "website/") + _FRONTEND
# Published artifacts that live under website/ but that Python asserts about.
# The OAuth Client ID Metadata Document is cross-checked against the pinned
# callback ports in tools/mcp_oauth.py, so editing it alone must still run the
# Python lane — otherwise dropping a redirect URI goes green here and breaks
# every CIMD login on main.
# website/docs/ and website/scripts/ are asserted about the same way. The docs
# tree generates llms.txt — the index every LLM (Hermes included, via the
# hermes-agent skill) reads to learn what Hermes can do — and
# tests/website/test_generate_llms_txt.py holds every page to appearing in it.
# Skipping Python on a docs-only PR is how the index drifted to 53% coverage.
_PY_RELEVANT_SITE = (
    "website/static/oauth/",
    "website/docs/",
    "website/scripts/",
)
# Cross-language contract files: data committed under a frontend tree that a
# pytest pins against the Python side (emitter inventory, command registry).
# Editing only the JSON in an apps/-only PR would otherwise skip the one test
# that can catch the drift, so these force the Python lane too.
_PY_RELEVANT_CONTRACT_FILES = {
    # tests/tui_runtime/contracts/test_generated.py (rendered from tui_runtime/contracts)
    "apps/shared/src/gateway-contract.generated.ts",
    "apps/shared/src/gateway-contract.openrpc.json",
    # tests/website/test_catalog_rules_mirror.py (docs page mirrors the canonical rules)
    "plugin-catalog/README.md",
}

# Supply-chain scan: files that can execute code at install/import time.
_SCAN_EXTS = (".py", ".pth")
_SCAN_FILES = {"setup.cfg", "pyproject.toml"}

# Bootstrap installer: the POSIX shell installer, the dev-checkout wrapper
# (the .rs/Cargo files are the ``rust`` lane's job). Changes here get the
# bootstrap-installer.yml lane — a real sandboxed install + stamp check.
_BOOTSTRAP_PATHS = ()
_BOOTSTRAP_FILES = {"scripts/install.sh", "setup-hermes.sh"}
# tests/_fixtures/ holds the conftest's platform gating, so it re-arms the lane too.

# Cross-language fixtures: one data file that tests in several languages read as
# their shared contract. Editing it is editing every consumer, so it selects each
# lane that runs one (a path-prefix rule would see only ``tests/`` -> python, and
# tests/ci/test_update_ci_routing.py finds the consumers in the tree and fails
# until every one is listed here.
_SHARED_FIXTURE_CONSUMERS: dict[str, tuple[str, ...]] = {
    # A7 rule 7: the update-marker parse / judge / release corpus.
    "tests/fixtures/update_marker_corpus.json": (
        "tests/hermes_cli/test_update_marker_corpus.py",  # Python: update_lock
    ),
}
# What a fixture inherits from its consumers: the lanes that run them as tests.
_FIXTURE_CONSUMER_LANES = ("python", "frontend")  # fork: rust lane removed with the Tauri crate

# The real SSH/Docker backend lane: the backends, the terminal tool that drives
# them, the tests it runs, the sshd it starts, and the runner's env allowlist
# that carries the sshd/image coordinates into each pytest process.
_REMOTE_BACKEND_PATHS = (
    "scripts/e2e/container_lifecycle.py",
    "tests/scripts/e2e/test_container_docker_live.py",
    "tools/environments/",
    "tools/terminal_tool",
    "tests/tools/test_ssh_environment.py",
    "tests/tools/test_docker_backend_real.py",
    "scripts/ci/start_test_sshd.sh",
    "scripts/ci/test_sshd_container_setup.sh",
    "scripts/run_tests.sh",
)

# The slow lanes and the paths that start each one on a pull request. A path
# belongs here when the lane is the only CI that exercises it for real: the
# suite itself, its harness, and the product code the suite exists to guard.
# Everything else reaches these lanes on the next push to main.
_DEP_MANIFESTS = ("pyproject.toml", "uv.lock", "setup.py")
_NPM_MANIFESTS = ("package.json", "package-lock.json")
# Shared pytest harness: a bad edit here can break every Python E2E suite.
_PY_TEST_HARNESS = (
    "tests/conftest.py",
    "tests/_fixtures/",
    "tests/fakes/",
    "tests/e2e/conftest.py",
    "tests/e2e/core/_",
    "scripts/run_tests.sh",
    "scripts/run_tests_parallel.py",
)
# suite imports the core suite's harness and provider).
# builds the bootstrap installer, so none of the three starts one.
# What `hermes update` runs outside the update_* module family: the steps of the
# pipeline (entry, lock, early recovery, completion tail, launchers, fleet
# restart/verify, Windows pause/resume) and the lock / marker / recovery state
# the update_* modules import. Editing any of these changes what a real update
# does, so the real-update suites (Linux e2e-upgrade and the Windows
# install + update journey, including its crash cells) must run on the PR.
_UPDATE_PIPELINE = (
    "hermes_cli/main.py",  # cmd_update: lock, pre-update backup, receipt boundary
    # Prefix: _early_recovery.py and its _early_recovery_* siblings (ZIP swap journal).
    "hermes_cli/_early_recovery",  # interrupted pull / shim restore at launch
    "hermes_cli/venv_sync.py",  # completion obligation + launch-time tail
    "hermes_cli/source_",  # source_completion/_build/_releases/_check/_stamp
    "hermes_cli/_launchers.py",
    "hermes_cli/release_channels.py",
    "hermes_cli/gitlock.py",  # git self-heal + partial-clone fetch
    "hermes_cli/process_identity.py",  # marker owner liveness
    "hermes_cli/runtime_state.py",
    "hermes_cli/relaunch.py",
    "hermes_cli/managed_uv.py",
    "hermes_cli/npm_engine.py",
    "hermes_cli/_scan_venv_blockers.py",
    "hermes_cli/dashboard_procs.py",
    "hermes_cli/git_pack_tidy.py",  # partial-clone pack tidy: every update's pre-fetch runs it
    "hermes_bootstrap.py",  # every launch's prepare_launch
    "hermes_constants.py",  # root home = update marker location
)
# Selectors are owned by the import graph, not remembered. The update
# transaction's own modules (what `hermes update` and the launch-time completion
# run between the lock and the receipt) are the entry points;
# tests/ci/test_update_ci_routing.py reads every repo module they import (AST,
# module level and lazy) and every build script they run, and fails until each
# one is routed to the suite below or is a _SHARED_HUBS entry.
_UPDATE_ENTRY_POINTS = (
    "hermes_cli/update_",
    # fork: encrypted update receipt custody (imported by update_vault/update_receipt)
    "hermes_security/io.py",
    "hermes_security/__init__.py",
    "hermes_security/errors.py",
    "hermes_security/vault.py",
    "hermes_security/migrate.py",
    "hermes_security/sqlite.py",
    "hermes_security/frames.py",
    "hermes_cli/_update_",
    "hermes_cli/source_",
    "hermes_cli/old_updater",
    "hermes_cli/_old_updater",
    "hermes_cli/post_update",
    "hermes_cli/venv_sync.py",
    "hermes_cli/_early_recovery",
    "hermes_cli/subcommands/update",
)
# What the entry points import, outside the update_* family and the pipeline above.
_UPDATE_DEPENDENCIES = (
    "hermes_cli/terminal_update.py",
    "hermes_platform/process_argv.py",
    "hermes_platform/child_processes.py",  # native updater custody and completion receipt ownership
    "hermes_security/handoff.py",
    "hermes_security/session_authority.py",
    "hermes_security/passphrase.py",  # wrapped credential readers and rotation metadata
    "hermes_security/vault_metadata.py",  # serialized key-slot/credential publication
    "hermes_cli/_subprocess_compat.py",  # update git env, process-tree kill, PM git exposure
    "hermes_cli/local_runtime/processes.py",  # bounded probes' spawn_server/job custody
    "agent/deadline.py",  # bounded probes' process-tree timeout cleanup
    # migrate_all_homes' second-hop provider/profile decisions, run on every update. Its
    # plugin-install branch (plugins_cmd, plugins_cmd_install) runs only for a home whose
    # configured memory provider left core; no update journey's home has one, so those
    # modules stay with the unit lane (tests/ci/test_update_transitive_routing.py).
    "agent/memory_provider.py",
    "pm/plugins_state.py",
    "pm/install.py",  # also sealed()/lazy_installs_allowed(): every update's default-tool install
    "hermes_cli/memory_provider_migration.py",
    "hermes_cli/left_core_migration.py",  # source_build migrates plugins that left core
    "hermes_cli/bundled_app.py",
    "hermes_cli/github_api.py",  # source_check's release lookup
    "hermes_cli/build_info.py",
    "hermes_cli/image_provenance.py",
    "hermes_cli/backup.py",  # pre-update backup
    "hermes_cli/backup_cron_prompts.py",  # restore_cron_prompt_fields_if_degraded (update_cmd_config)
    "hermes_cli/backup_restore.py",
    "hermes_cli/relay_plugin_migrate.py",
    "hermes_cli/macos_tcc_anchor.py",
    "hermes_cli/model_catalog.py",
    "hermes_cli/sqlite_runtime.py",
    "hermes_cli/sqlite_safe_read.py",
    "hermes_cli/sizefmt.py",
    "hermes_cli/tools_config_cua.py",
    "hermes_cli/_startup_fast.py",
    "hermes_cli/_parser.py",
    "hermes_cli/plugin_catalog.py",
    "hermes_cli/steward.py",
    "hermes_cli/observability/shared_metrics_update.py",
    "hermes_cli/main_install_repair.py",
    "hermes_logging.py",
    "hermes_platform/host/__init__.py",
    "hermes_platform/host/facts.py",
    "hermes_platform/resolver/__init__.py",  # update_cmd_commit's interpreter lookup
    "hermes_platform/resolver/base.py",
    "hermes_platform/resolver/core.py",
    "agent/curator.py",
    "plugins/memory/__init__.py",
    "tools/checkpoint_maintenance.py",
    "tools/skills_sync.py",
    "tools/environments/local_env_policy.py",
    "pm/progress.py",
    "scripts/build/",
)
# General-purpose modules an entry point imports but that half the product
# imports too (>= HUB_MIN_IMPORTERS product modules, checked by the test). The
# unit lanes cover them on every PR and the update suites on every push to main;
# routing each config.py / utils.py edit through the update suites would make
# them run on most PRs. Never an update-specific module: own those above.
HUB_MIN_IMPORTERS = 25
_SHARED_HUBS = frozenset({
    "hermes_cli/__init__.py",
    "hermes_cli/config.py",
    "hermes_cli/profiles.py",
    "hermes_cli/version_info.py",
    "utils.py",
    "hermes_state/__init__.py",
    "agent/__init__.py",
    "tools/environments/local.py",
    "hermes_constants.py",
    "pm/__init__.py",
    "pm/paths.py",
    "pm/environments.py",
})
_E2E_LANES: dict[str, tuple[str, ...]] = {
    "e2e": (
        *_PY_TEST_HARNESS,
        "tests/e2e/",
        # The state.db torture chamber and the compaction/exactly-once
        # suites are the only tests that run real concurrent writers.
        "hermes_state",
    ),
    "e2e_upgrade": (
        *_PY_TEST_HARNESS,
        *_DEP_MANIFESTS,
        "tests/e2e/core/upgrade/",
        "tests/compat/",
        "pm/",
        "scripts/install.",
        "setup-hermes.sh",
        "hermes_cli/update_",
        "hermes_cli/_update_",
        "hermes_cli/home_data_layout.py",   # fork: encrypted custody chain
        "hermes_cli/config_backups.py",
        "hermes_cli/old_updater",
        # The downgrade refusal (hermes_cli/update_downgrade_guard.py) reads state.db through it.
        "hermes_state/holders.py",
        "hermes_cli/_old_updater",
        "hermes_cli/post_update",
        "hermes_cli/config_migrations",
        "hermes_cli/subcommands/update",
        "hermes_cli/install_",
        "hermes_cli/_install_",
        "hermes_cli/main_install",
        *_UPDATE_PIPELINE,
        *_UPDATE_ENTRY_POINTS,
        *_UPDATE_DEPENDENCIES,
    ),
}

def _with_package_inits(paths: tuple[str, ...]) -> tuple[str, ...]:
    """Importing a/b/c.py runs a/__init__.py and a/b/__init__.py first, so a routed module
    routes the package inits above it. A shared hub's inits run whenever the hub is imported, so
    they are routed too; only an init that is itself a hub stays out."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    inits = [f"{'/'.join(parts[:i])}/__init__.py"
             for p in (*paths, *sorted(_SHARED_HUBS)) if p.endswith(".py")
             for parts in [p.split("/")[:-1]] for i in range(1, len(parts) + 1)]
    return tuple(dict.fromkeys([*paths, *(i for i in inits if i not in _SHARED_HUBS
                                          and os.path.isfile(os.path.join(root, i)))]))

for _lane in ("e2e_upgrade",):
    _E2E_LANES[_lane] = _with_package_inits(_E2E_LANES[_lane])
# The upgrade journeys are their own lane; editing one does not start ``e2e``.
_E2E_LANE_EXCLUDES = {
    "e2e": ("tests/e2e/core/upgrade/",),
}
_DOCKER_PATHS = (*_DOCKER_META, *_DEP_MANIFESTS, *_NPM_MANIFESTS, "pm/", "tests/docker/")
_NIX_LANE_PATHS = (*_NIX_PATHS, *_NIX_FILES, *_DEP_MANIFESTS, *_NPM_MANIFESTS)

# A pull request with this label runs every slow lane, whatever it touches.
RUN_E2E_LABEL = "run-e2e"

def _is_docs(p: str) -> bool:
    if p.startswith(("skills/", "optional-skills/")):
        return False
    return p.endswith((".md", ".mdx")) or p.startswith("docs/") or p.startswith("LICENSE")

def _is_nix(p: str) -> bool:
    return p.startswith(_NIX_PATHS) or p in _NIX_FILES

def _py_irrelevant(p: str) -> bool:
    if p.startswith(_PY_RELEVANT_SITE) or p in _PY_RELEVANT_CONTRACT_FILES:
        return False
    return (
        _is_docs(p)
        or p in _ROOT_NPM
        or p.startswith(_PY_SKIP)
        or p.startswith(_DOCKER_META)
        or _is_nix(p)
    )

def _is_scan(p: str) -> bool:
    return p.endswith(_SCAN_EXTS) or p in _SCAN_FILES

def _slow_lanes(files: list[str]) -> dict[str, bool]:
    """The slow lanes a pull request with this diff runs; see ``_E2E_LANES``."""
    lanes = {
        lane: any(
            f.startswith(paths) and not f.startswith(_E2E_LANE_EXCLUDES.get(lane, ()))
            for f in files
        )
        for lane, paths in _E2E_LANES.items()
    }
    lanes["docker"] = any(f.startswith(_DOCKER_PATHS) for f in files)
    lanes["nix"] = any(f.startswith(_NIX_LANE_PATHS) for f in files)
    return lanes

def classify(files: list[str], run_e2e: bool = False) -> dict[str, bool]:
    """Map changed paths to ``{lane: should_run}``.

    ``run_e2e`` is the pull request's ``run-e2e`` label: it turns every slow
    lane on.
    """
    files = [f.strip() for f in files if f.strip()]
    python = any(not _py_irrelevant(f) for f in files)
    frontend = any(
        f.startswith(_FRONTEND) or f in _ROOT_NPM or f in _FRONTEND_FILES
        or f.startswith("tests-js/")
        or (f.startswith("scripts/build/") and f.endswith((".mjs", ".js", ".ts")))
        for f in files
    )
    deps = any(f == "pyproject.toml" for f in files)
    npm_lock = any(f.split("/")[-1] == "package-lock.json" for f in files)
    docker_meta = any(f.startswith(_DOCKER_META) for f in files)

    ret = {
        "python": python,
        "docker_meta": docker_meta,
        "frontend": frontend,
        "site": any(f.startswith(_SITE) for f in files),
        "scan": any(_is_scan(f) for f in files),
        "deps": deps,
        "uv_lock": any(f in ("pyproject.toml", "uv.lock") for f in files),
        "npm_lock": npm_lock,
        "bootstrap": any(
            f.startswith(_BOOTSTRAP_PATHS) or f in _BOOTSTRAP_FILES for f in files
        ),
        "remote_backends": any(f.startswith(_REMOTE_BACKEND_PATHS) for f in files),
        **{lane: run_e2e or on for lane, on in _slow_lanes(files).items()},
    }
    consumers = [c for f in files for c in _SHARED_FIXTURE_CONSUMERS.get(f, ())]
    if consumers:
        inherited = classify(consumers)
        for lane in _FIXTURE_CONSUMER_LANES:
            ret[lane] = ret[lane] or inherited[lane]
    if not files or any(f.startswith(".github/") for f in files):
        ret["python"] = True
        ret["docker_meta"] = True
        ret["frontend"] = True
        ret["site"] = True
        ret["scan"] = True
        ret["deps"] = True
        ret["uv_lock"] = True
        ret["npm_lock"] = True
        ret["bootstrap"] = True
        ret["remote_backends"] = True
        ret.update(dict.fromkeys(_slow_lanes([]), True))
    return ret

def _event_payload() -> dict:
    """The Actions event payload, or ``{}`` when it is absent or unreadable."""
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return {}
    try:
        with open(event_path, encoding="utf-8-sig") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}

def _pull_request_number() -> str | None:
    """Read the PR number from the Actions event payload, if present."""
    number = (_event_payload().get("pull_request") or {}).get("number")
    return str(number) if number else None

def _gh_lines(*args: str) -> list[str] | None:
    """Run ``gh`` and return its stdout lines, or ``None`` when it fails."""
    try:
        completed = subprocess.run(
            ["gh", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]

def pull_request_changed_files() -> list[str]:
    """Recover the PR file list when the compare API returned nothing.

    ``detect-changes`` calls ``repos/.../compare/base...head`` with raw SHAs.
    A fork force-push can 404 for ~30s until GitHub attaches the new head SHA
    to the base repo, so the action fails open with an empty file list and
    runs every lane even when the change is narrow.

    The pull-request files endpoint already knows the PR's files (it is how
    this action used to classify), so use it as a fallback on pull_request
    events only. Push/dispatch keep the empty-diff fail-open.
    """
    if os.environ.get("EVENT_NAME") != "pull_request":
        return []
    repo = os.environ.get("REPO") or os.environ.get("GITHUB_REPOSITORY") or ""
    pr = _pull_request_number()
    if not repo or not pr:
        return []
    return _gh_lines("api", "--paginate", f"repos/{repo}/pulls/{pr}/files", "--jq", ".[].filename") or []

def pull_request_labels() -> list[str]:
    """The pull request's labels as they are now, not as the event saw them.

    A re-run replays the original event payload, so the payload alone would
    miss a ``run-e2e`` label added after the push. The API answer wins; the
    payload is the fallback when the API call fails.
    """
    if os.environ.get("EVENT_NAME") != "pull_request":
        return []
    repo = os.environ.get("REPO") or os.environ.get("GITHUB_REPOSITORY") or ""
    pr = _pull_request_number()
    if repo and pr:
        live = _gh_lines("api", f"repos/{repo}/pulls/{pr}", "--jq", ".labels[].name")
        if live is not None:
            return live
    labels = (_event_payload().get("pull_request") or {}).get("labels") or []
    return [label["name"] for label in labels if isinstance(label, dict) and label.get("name")]

def main() -> int:
    files = sys.stdin.read().splitlines()
    if not any(f.strip() for f in files):
        recovered = pull_request_changed_files()
        if recovered:
            print(
                f"compare API returned no files; recovered {len(recovered)} "
                "path(s) from the pull request files endpoint",
                file=sys.stderr,
            )
            files = recovered
    lanes = classify(files, run_e2e=RUN_E2E_LABEL in pull_request_labels())
    out = "\n".join(f"{key}={str(value).lower()}" for key, value in lanes.items())
    if dest := os.environ.get("GITHUB_OUTPUT"):
        with open(dest, "a", encoding="utf-8") as fh:
            fh.write(out + "\n")
    print(out)  # echo for local runs + CI step logs
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
