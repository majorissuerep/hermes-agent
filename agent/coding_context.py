"""Coding-context awareness: the single place that decides "are we coding?".

In a code workspace on an interactive surface Hermes adopts a **coding posture**: a
frozen :class:`RuntimeMode` built from a :class:`ContextProfile` (pure data). The
system prompt reads ``system_prompt_parts()``; the toolset collapses ONLY under opt-in
``focus`` (never strips a user-enabled toolset). ``agent.coding_context``: ``auto``
(default, prompt-only) / ``focus`` / ``on`` / ``off``. Resolved once, immutable; the
workspace snapshot is probed once per session and replayed through
``system_prompt_parts(workspace_block=...)`` on every later build (cache safety).
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from hermes_cli._subprocess_compat import bounded_git_probe

logger = logging.getLogger("hermes.coding_context")

CODING_TOOLSET = "coding"

# Surfaces where ``auto`` may adopt the posture; messaging platforms are deliberately absent.
INTERACTIVE_CODING_PLATFORMS = {"cli", "tui", "acp", "desktop", ""}
# Project-root signals (cheap filename checks) marking a code workspace even without git.
_PROJECT_MARKERS = (
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "package.json", "tsconfig.json", "deno.json",
    "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts", "Gemfile", "composer.json", "mix.exs",
    "pubspec.yaml", "CMakeLists.txt", "Makefile", "Dockerfile", "AGENTS.md", "CLAUDE.md", ".cursorrules",
)
# Agent-instruction files surfaced separately from manifests in the snapshot.
_CONTEXT_FILES = ("AGENTS.md", "CLAUDE.md", ".cursorrules")

# Extensions that make a manifest-less git repo a *code* workspace (a `git init` notes folder is not).
_CODE_EXTENSIONS = frozenset({
    ".py", ".pyi", ".ipynb", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt", ".kts",
    ".scala", ".rb", ".php", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".swift", ".m", ".mm", ".dart", ".ex", ".exs",
    ".lua", ".sh", ".bash", ".zsh", ".sql", ".vue", ".svelte", ".r", ".jl", ".hs", ".clj", ".erl", ".pl",
})
_CODE_SCAN_SKIP_DIRS = frozenset({
    ".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build", "target", ".next", ".turbo", "vendor",
})
# Bounded sweep: a code workspace reveals itself in the first handful of entries.
_CODE_SCAN_MAX_ENTRIES = 500
# Lockfile → package manager, checked in priority order.
_PY_LOCKFILES = (("uv.lock", "uv"), ("poetry.lock", "poetry"), ("Pipfile.lock", "pipenv"))
_JS_LOCKFILES = (
    ("pnpm-lock.yaml", "pnpm"), ("bun.lockb", "bun"), ("bun.lock", "bun"), ("yarn.lock", "yarn"), ("package-lock.json", "npm"),
)
# package.json scripts / Makefile targets worth surfacing as verify commands.
_VERIFY_TARGETS = ("test", "tests", "lint", "typecheck", "check", "build", "fmt", "format")
_MAX_VERIFY_COMMANDS = 8
_MAX_FACT_FILE_BYTES = 256 * 1024
_GIT_TIMEOUT = 2.5
# Per-model edit-format steering toward the `patch` mode each family was trained on (unknown
# families get nothing). GPT/Codex get V4A for ALL edits incl. single-file: codex-rs ships
# apply_patch as its ONLY editor. Anthropic and most open-weight coders were RL'd on str_replace
# editors. Substrings match the model id; aligned with TOOL_USE_ENFORCEMENT_MODELS.
_EDIT_FORMAT_GUIDANCE: dict[str, tuple[tuple[str, ...], str]] = {
    "patch": (
        ("gpt", "codex"),
        "- Edit format: author new files with `write_file`; for edits to "
        "existing code use `patch` with `mode='patch'` (V4A diff) — including "
        "single-file edits. It's the edit format you handle most reliably.",
    ),
    "replace": (
        ("claude", "sonnet", "opus", "haiku",
         "gemini", "gemma", "deepseek", "qwen", "kimi", "glm", "grok",
         "hermes", "llama", "mistral", "devstral", "minimax"),
        "- Edit format: author new files with `write_file`; for edits to "
        "existing code prefer `patch` in `mode='replace'` — match a unique "
        "snippet and swap it. Reach for `mode='patch'` (V4A) only when an edit "
        "genuinely spans several files at once.",
    ),
}

# Operating brief. Tool names referenced here are in the coding toolset and _HERMES_CORE_TOOLS.
CODING_AGENT_GUIDANCE = (
    "You are a coding agent pairing with the user inside their codebase. Prefer readable, conventional "
    "code and evidence over cleverness.\n"
    "\n"
    "Before editing:\n"
    "- Use `read_file` and `search_files` to inspect applicable AGENTS.md / CLAUDE.md / .cursorrules, the "
    "nearest similar implementation, definitions and callers. Verify APIs and dependencies in the "
    "repository; do not invent them. Batch independent lookups.\n"
    "- For PR work, search open issues and PRs for duplicate or overlapping work. Reuse or coordinate "
    "existing work; report unavailable search access. An explicit user request authorizes the task "
    "without requiring a labelled issue.\n"
    "- State the intended behavior and expected files. Keep one concern per change; aim for at most 5 "
    "files or 300 changed lines. Beyond that, split independent work or explain why the authorized change "
    "must stay together. Avoid unrelated dependencies, abstractions, renames and formatting.\n"
    "- For a bug, reproduce it with a failing behavioral test before fixing it where feasible; otherwise "
    "record the concrete reproduction and limitation. For performance, record baseline and after "
    "measurements with the same command and environment.\n"
    "\n"
    "Implement and review:\n"
    "- Apply edits with `patch` / `write_file`; code in chat is not a substitute. Match nearby naming and "
    "structure. Prefer explicit control flow over terse tricks; do not add speculative frameworks or "
    "fallbacks that hide errors.\n"
    "- Re-read after a failed patch. Two failures with one approach require a diagnosis and a materially "
    "different approach, not a larger blind rewrite. Preserve unrelated work.\n"
    "- Consider relevant empty/null, error, boundary, concurrency and authorization cases; map each to a "
    "test or an explicit reason it does not apply. Trace failures, cleanup and callers. Fix the root "
    "cause within scope.\n"
    "- Use existing auth, validation, secret-storage and feature-flag patterns. Never expose credentials "
    "in code, tests, commands, logs or PRs. Explain failure behavior, existing monitoring and rollback "
    "for risky changes.\n"
    "- Review the finished diff separately from implementation for logic, error handling, security and "
    "production risks. When available and authorized, request a fresh-context review with only the task "
    "and diff, then verify and address findings yourself. Otherwise report self-review honestly.\n"
    "\n"
    "Verify and deliver:\n"
    "- Use `terminal` for git and checks. Run the repository's required build, tests, lint, types and "
    "secret checks that apply to the change. Check exit codes and final output against the current "
    "revision; a previous green run is not current evidence. Report unavailable checks as unverified.\n"
    "- Before opening or updating a ready PR, all applicable required local checks must pass. Before "
    "merging, also require current required CI checks to pass; never bypass failing, pending or missing "
    "checks. Never weaken checks to get green. A draft with known failures requires explicit user "
    "direction. Recheck after every changed revision.\n"
    "- Start the PR description with the user-visible effect and a real issue link when one exists. "
    "Include exact commands/results, regression or performance evidence, risks, rollback and review "
    "findings. Do not fabricate links or passing results.\n"
    "- During authorized review work, address every actionable comment with its fixing commit or a "
    "reason; leave thread resolution to the reviewer unless explicitly asked. After two failed attempts "
    "at one approach or three red CI runs, stop repeating it, report evidence and alternatives, and "
    "continue independent work. Ask for direction only when no safe alternative is authorized.\n"
    "- Terminal working directory and exported variables persist: reuse established setup.\n"
    "- Track multi-step work with `todo_list`. Reference code as `path:line` instead of pasting whole "
    "files.\n"
    "\n"
    "Respect the user's repo: commit, push and merge only within established authorization; never rewrite "
    "unrelated history or expose secrets. The Workspace block is a session-start snapshot: recheck git "
    "status and branch before relying on it. Keep cached prompts stable within a conversation. Report "
    "what changed, what was checked and what remains unverified; passing checks do not prove long-term "
    "production quality."
)
_TODO_SENTENCE = (
    "- Track multi-step work with `todo_list`. Reference code as "
    "`path:line` instead of pasting whole files."
)
_NO_TODO_SENTENCE = "- Reference code as `path:line` instead of pasting whole files."
# Clearly non-coding skill categories (deny-list; coding-adjacent and custom ones keep full entries).
_NON_CODING_SKILL_CATEGORIES = (
    "apple", "communication", "cooking", "creative", "email", "finance", "gaming", "gifs", "health", "media",
    "music", "note-taking", "productivity", "shopping", "smart-home", "social-media", "travel", "yuanbao",
)
_MODE_ALIASES = {
    **dict.fromkeys(("focus", "strict", "lean"), "focus"),
    **dict.fromkeys(("on", "true", "yes", "1", "always"), "on"),
    **dict.fromkeys(("off", "false", "no", "0", "never"), "off"),
}


@dataclass(frozen=True)
class ContextProfile:
    """A named operating posture (pure data). ``toolset``: collapse target under ``focus``
    (``None`` keeps the platform default). ``compact_skill_categories``: DEMOTED to
    names-only under ``focus`` — deny-list, never hidden, so recall keeps working."""

    name: str
    toolset: Optional[str] = None
    guidance: str = ""
    model_hint: Optional[str] = None
    compact_skill_categories: tuple[str, ...] = ()


GENERAL_PROFILE = ContextProfile(name="general")
CODING_PROFILE = ContextProfile(
    name="coding", toolset=CODING_TOOLSET, guidance=CODING_AGENT_GUIDANCE, model_hint="coding",
    compact_skill_categories=_NON_CODING_SKILL_CATEGORIES,
)


# ── Detection helpers ───────────────────────────────────────────────────────

def _model_family(model: Optional[str]) -> Optional[str]:
    """Edit-format family key for a model id, or ``None`` (neutral wording applies)."""
    lowered = (model or "").lower()
    for family, (needles, _line) in _EDIT_FORMAT_GUIDANCE.items():
        if lowered and any(n in lowered for n in needles):
            return family
    return None


def _agent_config_value(config: Optional[dict[str, Any]], key: str, default: Any, *, readonly: bool) -> Any:
    """``config["agent"][key]``, loading config when none was passed."""
    if config is None:
        try:
            from hermes_cli.config import load_config, load_config_readonly
            config = load_config_readonly() if readonly else load_config()
        except Exception:
            config = {}
    return ((config or {}).get("agent", {}) or {}).get(key, default)


def _coding_mode(config: Optional[dict[str, Any]]) -> str:
    """Normalized ``agent.coding_context`` mode (auto/focus/on/off)."""
    raw = _agent_config_value(config, "coding_context", "auto", readonly=True)
    return _MODE_ALIASES.get(str(raw).strip().lower(), "auto")


def _resolve_cwd(cwd: Optional[str | Path]) -> Path:
    if cwd:
        return Path(cwd).expanduser()
    try:
        from agent.runtime_cwd import resolve_agent_cwd
        return resolve_agent_cwd()
    except Exception:
        return Path(os.getcwd())


def _git_root(cwd: Path) -> Optional[Path]:
    current = cwd.resolve()
    return next((p for p in (current, *current.parents) if (p / ".git").exists()), None)


def _home() -> Optional[Path]:
    try:
        return Path.home().resolve()
    except (OSError, RuntimeError):
        return None


def _marker_root(cwd: Path) -> Optional[Path]:
    """Nearest ancestor (≤6 levels) that looks like a project root, or ``None``. ``$HOME``
    and the shared temp root are skipped: a Makefile/AGENTS.md in the home dir is global
    config, and a stray manifest in /tmp must not flip every session under it."""
    current = cwd.resolve()
    try:
        temp_root = Path(tempfile.gettempdir()).resolve()
    except Exception:
        temp_root = None
    skip = (_home(), temp_root)
    for parent in (current, *current.parents)[:7]:
        if parent not in skip and any((parent / marker).exists() for marker in _PROJECT_MARKERS):
            return parent
    return None


def _has_code_files(root: Path) -> bool:
    """Bounded check for source files in the root and its immediate subdirs."""
    seen = 0
    stack = [(root, True)]
    while stack:
        directory, is_root = stack.pop()
        try:
            entries = os.scandir(directory)
        except OSError:
            continue
        with entries:
            for entry in entries:
                seen += 1
                if seen > _CODE_SCAN_MAX_ENTRIES:
                    return False
                try:
                    if entry.is_file():
                        if os.path.splitext(entry.name)[1].lower() in _CODE_EXTENSIONS:
                            return True
                    elif is_root and entry.is_dir() and entry.name not in _CODE_SCAN_SKIP_DIRS and not entry.name.startswith("."):
                        stack.append((Path(entry.path), False))
                except OSError:
                    continue
    return False


def _detect_profile(mode: str, platform: str, cwd: Path) -> ContextProfile:
    """``auto``/``focus``: coding when the surface is interactive AND the cwd is a code
    workspace (project root, or a git repo that actually holds code; a repo rooted at
    ``$HOME`` is NOT a signal). ``on``/``off`` force. Not memoized: one gateway serves many cwds."""
    if mode == "off":
        return GENERAL_PROFILE
    if mode == "on":
        return CODING_PROFILE
    if platform and platform.strip().lower() not in INTERACTIVE_CODING_PLATFORMS:
        return GENERAL_PROFILE
    if _marker_root(cwd) is not None:
        return CODING_PROFILE
    git_root = _git_root(cwd)
    if git_root is not None and git_root != _home() and _has_code_files(git_root):
        return CODING_PROFILE
    return GENERAL_PROFILE


def _enabled_mcp_servers(config: Optional[dict[str, Any]]) -> list[str]:
    """Names of MCP servers the user has enabled — kept in the coding posture."""
    try:
        from hermes_cli.config import read_raw_config
        from tools.mcp_tool_common import mcp_server_enabled
        servers = read_raw_config().get("mcp_servers") or {}
        return [
            str(name) for name, cfg in servers.items()
            if isinstance(cfg, dict) and mcp_server_enabled(cfg)
        ]
    except Exception:
        return []


# ── RuntimeMode (the seam) ──────────────────────────────────────────────────

@dataclass(frozen=True)
class RuntimeMode:
    """The resolved operating posture for a session; immutable, built once via
    :func:`resolve_runtime_mode` and never re-resolved mid-session (prompt cache).
    ``config_mode``: normalized ``agent.coding_context`` (toolset collapse gated on
    ``focus``); ``model``: steers edit-format guidance only; ``instructions``:
    ``agent.coding_instructions``."""

    profile: ContextProfile
    surface: str
    cwd: Path
    config_mode: str = "auto"
    model: Optional[str] = None
    instructions: str = ""

    @property
    def kind(self) -> str:
        return self.profile.name

    @property
    def is_coding(self) -> bool:
        return self.profile.name == CODING_PROFILE.name

    def toolset_selection(self, config: Optional[dict[str, Any]] = None) -> Optional[list[str]]:
        """Toolset list (only under ``focus``), or ``None`` to keep the platform default. Callers
        apply it only when the user hasn't pinned a selection (``--toolsets``, ``HERMES_TUI_TOOLSETS``)."""
        if self.config_mode != "focus" or self.profile.toolset is None:
            return None
        return [self.profile.toolset, *_enabled_mcp_servers(config)]

    def system_prompt_parts(self, valid_tool_names=None, workspace_block: Optional[str] = None) -> tuple[list[str], list[str], list[str]]:
        """Return (prefix, workspace, trailing) posture blocks in the historical flat order —
        brief, snapshot, operator instructions — so prompt assembly can put a cache boundary
        before the snapshot without changing persisted bytes. The brief carries the model-family
        edit-format nudge (one cached string); ``valid_tool_names`` drops the ``todo_list``
        sentence when that tool isn't loaded; operator instructions ride their own block so
        the brief stays byte-stable. ``workspace_block`` replays a snapshot the caller already
        pinned at session start (``""`` = no workspace) instead of re-running the git probe;
        ``None`` probes."""
        if not self.is_coding:
            return [], [], []
        prefix: list[str] = []
        if self.profile.guidance:
            brief = self.profile.guidance
            if valid_tool_names is not None and "todo_list" not in valid_tool_names:
                brief = brief.replace(_TODO_SENTENCE, _NO_TODO_SENTENCE)
            family = _model_family(self.model)
            if family is not None:
                brief = f"{brief}\n{_EDIT_FORMAT_GUIDANCE[family][1]}"
            prefix.append(brief)
        workspace = build_coding_workspace_block(self.cwd) if workspace_block is None else workspace_block
        trailing = [f"Operator instructions (from config):\n{self.instructions}"] if self.instructions else []
        return prefix, [workspace] if workspace else [], trailing

    def system_blocks(self) -> list[str]:
        """Posture blocks as one flat list in historical order."""
        prefix, workspace, trailing = self.system_prompt_parts()
        return [*prefix, *workspace, *trailing]

    def compact_skill_categories(self) -> frozenset[str]:
        """Skill categories to demote to names-only in the skill index. Gated on ``focus``
        like the toolset collapse (index changes under ``auto`` proved too surprising).
        Demoted, never hidden: pruning caused silent capability loss."""
        if not self.is_coding or self.config_mode != "focus":
            return frozenset()
        return frozenset(self.profile.compact_skill_categories)


def resolve_runtime_mode(
    *, platform: Optional[str] = None, cwd: Optional[str | Path] = None, config: Optional[dict[str, Any]] = None,
    model: Optional[str] = None,
) -> RuntimeMode:
    """Resolve the operating posture once (a handful of ``stat`` calls) — the single entry
    point every domain should call; the result is safe to hold for the session. ``model``
    only steers edit-format guidance; ``agent.coding_instructions`` (str or list) becomes
    the trailing block so a user can pin workflow rules without editing the shipped brief."""
    resolved_cwd = _resolve_cwd(cwd)
    mode = _coding_mode(config)
    raw = _agent_config_value(config, "coding_instructions", "", readonly=False)
    items = raw if isinstance(raw, (list, tuple)) else [raw or ""]
    instructions = "\n".join(str(item).strip() for item in items if str(item).strip())
    return RuntimeMode(
        profile=_detect_profile(mode, (platform or "").strip().lower(), resolved_cwd),
        surface=platform or "",
        cwd=resolved_cwd,
        config_mode=mode,
        model=model,
        instructions=instructions,
    )


# ── Functional API (thin wrappers over RuntimeMode) ──────────────────────────

def is_coding_context(*, platform: Optional[str] = None, cwd: Optional[str | Path] = None, config: Optional[dict[str, Any]] = None) -> bool:
    """Whether Hermes should operate in its coding posture right now."""
    return resolve_runtime_mode(platform=platform, cwd=cwd, config=config).is_coding


def coding_selection(*, platform: Optional[str] = None, cwd: Optional[str | Path] = None, config: Optional[dict[str, Any]] = None) -> Optional[list[str]]:
    """Toolset selection for the coding posture (``None`` unless ``focus`` and active)."""
    return resolve_runtime_mode(platform=platform, cwd=cwd, config=config).toolset_selection(config)


def coding_system_prompt_parts(
    *, platform: Optional[str] = None, cwd: Optional[str | Path] = None, config: Optional[dict[str, Any]] = None,
    model: Optional[str] = None, valid_tool_names=None, workspace_block: Optional[str] = None,
) -> tuple[list[str], list[str], list[str]]:
    """Return coding prefix, workspace snapshot, and trailing guidance.  ``workspace_block``
    replays the caller's pinned session-start snapshot instead of probing git again."""
    mode = resolve_runtime_mode(platform=platform, cwd=cwd, config=config, model=model)
    return mode.system_prompt_parts(valid_tool_names=valid_tool_names, workspace_block=workspace_block)


def coding_compact_skill_categories(*, platform: Optional[str] = None, cwd: Optional[str | Path] = None, config: Optional[dict[str, Any]] = None) -> frozenset[str]:
    """Skill categories the active posture demotes to names-only (empty outside ``focus``)."""
    return resolve_runtime_mode(platform=platform, cwd=cwd, config=config).compact_skill_categories()


# ── git/workspace probe ─────────────────────────────────────────────────────

def _git(cwd: Path, *args: str) -> str:
    """``git -C <cwd> <args>`` → stripped stdout, or ``""`` on any failure. bounded_git_probe
    bounds post-kill cleanup on Windows — plain ``subprocess.run(timeout=...)`` deadlocked
    when a killed git left a suspended descendant holding the pipe handles.

    See #66037.
    """
    return bounded_git_probe(["git", "-C", str(cwd), *args], timeout=_GIT_TIMEOUT)


def _parse_status(porcelain: str) -> tuple[dict[str, str], dict[str, int]]:
    """Parse ``git status --porcelain=2 --branch`` into branch + counts."""
    branch: dict[str, str] = {}
    counts = {"staged": 0, "modified": 0, "untracked": 0, "conflicts": 0}
    for line in porcelain.splitlines():
        if line.startswith(("# branch.head", "# branch.upstream")):
            parts = line.split(maxsplit=2)
            branch[parts[1].removeprefix("branch.")] = parts[-1]
        elif line.startswith("# branch.ab"):
            parts = line.split()
            branch["ahead"], branch["behind"] = parts[2].lstrip("+"), parts[3].lstrip("-")
        elif line.startswith(("1 ", "2 ")):
            xy = line.split(maxsplit=2)[1]
            counts["staged"] += xy[0] != "."
            counts["modified"] += xy[1] != "."
        elif line.startswith(("u ", "? ")):
            counts["conflicts" if line[0] == "u" else "untracked"] += 1
    return branch, counts


def _read_small(path: Path) -> str:
    """Read a small text file, or ``""`` — never raises, never reads huge files."""
    try:
        if not path.is_file() or path.stat().st_size > _MAX_FACT_FILE_BYTES:
            return ""
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


@dataclass(frozen=True)
class ProjectFacts:
    """Structured project facts — exposed so non-prompt consumers (desktop verify UI) don't re-detect."""

    manifests: list[str]
    package_managers: list[str]
    verify_commands: list[str]
    context_files: list[str]


def detect_project_facts(root: Path) -> ProjectFacts:
    """Detect manifests, package manager(s), verify commands, context files (single source of truth)."""
    verify: list[str] = []
    if (root / "scripts" / "run_tests.sh").is_file():
        verify.append("scripts/run_tests.sh")
    if (root / "package.json").is_file():
        try:
            scripts = json.loads(_read_small(root / "package.json") or "{}").get("scripts") or {}
        except (json.JSONDecodeError, AttributeError):
            scripts = {}
        js_pm = next((pm for lock, pm in _JS_LOCKFILES if (root / lock).is_file()), "npm")
        verify.extend(f"{js_pm} run {name}" for name in _VERIFY_TARGETS if name in scripts)
    if (root / "pytest.ini").is_file() or "[tool.pytest" in _read_small(root / "pyproject.toml"):
        verify.append("pytest")
    makefile = _read_small(root / "Makefile")
    verify.extend(
        f"make {name}" for name in _VERIFY_TARGETS
        if makefile and re.search(rf"^{re.escape(name)}\s*:", makefile, re.MULTILINE)
    )
    return ProjectFacts(
        manifests=[m for m in _PROJECT_MARKERS if m not in _CONTEXT_FILES and (root / m).is_file()],
        package_managers=list(dict.fromkeys(
            pm for lock, pm in (*_PY_LOCKFILES, *_JS_LOCKFILES) if (root / lock).is_file()
        )),
        verify_commands=list(dict.fromkeys(verify))[:_MAX_VERIFY_COMMANDS],
        context_files=[c for c in _CONTEXT_FILES if (root / c).is_file()],
    )


def _workspace_roots(cwd: Optional[str | Path]) -> tuple[Optional[Path], Optional[Path]]:
    """(git_root, workspace_root) for *cwd*; workspace root is git root else marker root."""
    resolved = _resolve_cwd(cwd)
    git_root = _git_root(resolved)
    return git_root, git_root or _marker_root(resolved)


def project_facts_for(cwd: Optional[str | Path] = None) -> Optional[dict[str, Any]]:
    """Structured project facts for ``cwd`` (desktop verify UI) — ``None`` outside a workspace."""
    _, root = _workspace_roots(cwd)
    if root is None:
        return None
    f = detect_project_facts(root)
    return {
        "root": str(root),
        "manifests": f.manifests,
        "packageManagers": f.package_managers,
        "verifyCommands": f.verify_commands,
        "contextFiles": f.context_files,
    }


WORKSPACE_BLOCK_HEADER = "Workspace (snapshot at session start — re-check with `git` before acting on it):"


def build_coding_workspace_block(cwd: Optional[str | Path] = None) -> str:
    """Workspace snapshot for the system prompt (empty outside a workspace): git state when
    in a repo, plus project facts — so marker-only (non-git) projects still get one."""
    git_root, root = _workspace_roots(cwd)
    if root is None:
        return ""
    lines = [
        WORKSPACE_BLOCK_HEADER,
        f"- Root: {root}",
    ]
    if git_root is not None:
        # ``status`` hashes re-timestamped files through the repo-named clean filter; skip it when
        # the filter overrides cannot be discovered rather than run it half-hardened.
        from hermes_cli._subprocess_compat import noninteractive_repo_git_env
        status_env = noninteractive_repo_git_env(root)
        status = "" if status_env is None else bounded_git_probe(
            ["git", "-C", str(root), "status", "--porcelain=2", "--branch"], timeout=_GIT_TIMEOUT, env=status_env)
        branch, counts = _parse_status(status)
        head = branch.get("head", "")
        if head == "(detached)":
            lines.append("- Branch: (detached HEAD)")
        elif head:
            ahead, behind = branch.get("ahead", "0"), branch.get("behind", "0")
            upstream = f" \u2192 {branch['upstream']}" if branch.get("upstream") else ""
            ab = f" (ahead {ahead}, behind {behind})" if upstream and (ahead, behind) != ("0", "0") else ""
            lines.append(f"- Branch: {head}{upstream}{ab}")

        # Linked worktree: say so (branches/stashes are shared state) but do NOT expose
        # the primary tree path — a second absolute path makes the model run commands
        # in the wrong directory.
        git_dir, common_dir = _git(root, "rev-parse", "--git-dir"), _git(root, "rev-parse", "--git-common-dir")
        if git_dir and common_dir and Path(git_dir).resolve() != Path(common_dir).resolve():
            lines.append("- Worktree: linked (git state shared with primary tree)")

        dirty = [f"{n} {label}" for label, n in counts.items() if n]
        lines.append(f"- Status: {', '.join(dirty) if dirty else 'clean'}")

        recent = _git(root, "log", "-3", "--pretty=%h %s")
        if recent:
            lines.extend(["- Recent commits:", *(f"    {c}" for c in recent.splitlines())])

    f = detect_project_facts(root)
    if f.manifests:
        managers = f" ({'/'.join(f.package_managers)})" if f.package_managers else ""
        lines.append(f"- Project: {', '.join(f.manifests[:6])}{managers}")
    if f.verify_commands:
        lines.append(f"- Verify: {'; '.join(f.verify_commands)}")
    if f.context_files:
        lines.append(f"- Context files: {', '.join(f.context_files)}")
    return "\n".join(lines)
