"""Grant-time scanning: what would a grant actually expose?

Before a path is granted, it is walked (bounded by file count, bytes and time) and two
kinds of finding are reported:

- **sensitive files** by name/location — SSH/GPG keys, cloud and registry credentials,
  ``.env`` files, password databases, browser credential stores, a Hermes home;
- **secrets in content** — every line the output redactor (``agent.redact``) would
  mask: API keys, tokens, private-key blocks, credential assignments. Using the same
  detector as output redaction keeps "secret" meaning one thing across Hermes.

A clean report is not a guarantee; it is the evidence the user sees before consenting.
"""

from __future__ import annotations

import fnmatch
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

_SENSITIVE_NAMES = (
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "id_ecdsa_sk", "id_ed25519_sk",
    "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore", "*.kdbx", "*.kdb", "*.agilekeychain",
    ".env", ".env.*", "*.env", ".envrc", ".netrc", "_netrc", ".pgpass", ".npmrc", ".pypirc",
    ".git-credentials", ".my.cnf", "credentials", "credentials.json", "credentials.db",
    "auth.json", "token.json", "*.keychain", "*.keychain-db", "Login Data", "Cookies",
    "key4.db", "logins.json", "wallet.dat", "secrets.yaml", "secrets.yml", "*.tfstate",
    "service-account*.json", ".hermes-vault",
)
_EXAMPLE_SUFFIXES = (".example", ".sample", ".template", ".dist")
_SENSITIVE_DIRS = (".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".password-store",
                   "gcloud", "gh", "1Password", "keyrings")
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache",
              ".tox", "dist", "build", "target", ".next", ".cache"}
_MAX_FINDINGS_PER_FILE = 5
# Content hashes, not credentials.
_LOCKFILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "uv.lock", "poetry.lock", "Cargo.lock",
              "Gemfile.lock", "composer.lock", "go.sum", "Pipfile.lock", "flake.lock", "bun.lock"}
# Source code: the redactor's code mode skips the assignment heuristics that flag
# ``MAX_TOKENS = 4096``-style identifiers while still catching real key formats.
_CODE_SUFFIXES = {".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java",
                  ".kt", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".scala",
                  ".sh", ".bash", ".zsh", ".lua", ".md", ".rst", ".html", ".css", ".vue", ".svelte"}


@dataclass(frozen=True)
class Finding:
    path: str
    kind: str      # "sensitive-file" | "sensitive-dir" | "secret" | "hermes-home"
    detail: str


@dataclass
class ScanReport:
    root: str
    exists: bool = True
    files_scanned: int = 0
    bytes_scanned: int = 0
    truncated: bool = False
    findings: list[Finding] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return self.exists and not self.findings

    def summary_lines(self, limit: int = 12) -> list[str]:
        if not self.exists:
            return [f"{self.root}: does not exist (the grant confers nothing until it does)"]
        head = (f"Scanned {self.files_scanned} file(s), {self.bytes_scanned // 1024} KiB"
                + (" (stopped early: scan budget reached)" if self.truncated else ""))
        if not self.findings:
            return [head, "No sensitive files or secrets detected."]
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f.kind] = counts.get(f.kind, 0) + 1
        lines = [head, "Findings: " + ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))]
        lines += [f"  - [{f.kind}] {f.path}: {f.detail}" for f in self.findings[:limit]]
        if len(self.findings) > limit:
            lines.append(f"  ... and {len(self.findings) - limit} more")
        return lines


def _is_sensitive_name(name: str) -> bool:
    if name.endswith(_EXAMPLE_SUFFIXES) or name.endswith(".pub"):
        return False
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in _SENSITIVE_NAMES)


def _secret_lines(text: str, *, code: bool) -> list[int]:
    from agent.redact import redact_sensitive_text
    def redact(chunk: str) -> str:
        return redact_sensitive_text(chunk, force=True, code_file=code, secret_file=not code)
    if redact(text) == text:
        return []
    hits = []
    for number, line in enumerate(text.splitlines(), 1):
        if "PRIVATE KEY-----" in line or redact(line) != line:
            hits.append(number)
            if len(hits) >= _MAX_FINDINGS_PER_FILE:
                break
    return hits


def _scan_file(path: Path, report: ScanReport, max_file_bytes: int) -> None:
    rel = str(path)
    if _is_sensitive_name(path.name):
        report.findings.append(Finding(rel, "sensitive-file", "credential/key file by name"))
    try:
        with open(path, "rb") as handle:
            data = handle.read(max_file_bytes)
    except OSError:
        return
    report.files_scanned += 1
    report.bytes_scanned += len(data)
    if b"\x00" in data[:8192] or path.name in _LOCKFILES or path.name.endswith(_EXAMPLE_SUFFIXES):
        return
    lines = _secret_lines(data.decode("utf-8", errors="replace"), code=path.suffix.lower() in _CODE_SUFFIXES)
    if lines:
        report.findings.append(Finding(rel, "secret", "line(s) " + ", ".join(map(str, lines))))


def scan_path(path: str, *, max_files: int = 20000, max_file_bytes: int = 256 << 10,
              max_total_bytes: int = 256 << 20, time_budget: float = 15.0) -> ScanReport:
    from hermes_constants import get_hermes_home
    root = Path(os.path.abspath(os.path.expanduser(path)))
    report = ScanReport(str(root))
    if not root.exists():
        report.exists = False
        return report
    hermes_home = Path(get_hermes_home()).resolve()
    resolved = root.resolve()
    if resolved == hermes_home or hermes_home in resolved.parents or resolved in hermes_home.parents:
        report.findings.append(Finding(str(root), "hermes-home",
                                       "contains Hermes state (vault, sessions, credentials)"))
    import hermes_constants
    hermes_code = Path(hermes_constants.__file__).resolve().parent
    if resolved == hermes_code or hermes_code in resolved.parents or resolved in hermes_code.parents:
        report.findings.append(Finding(str(root), "hermes-code",
                                       "contains Hermes' own code/venv: write access lets the model change "
                                       "what Hermes runs after a restart"))
    if root.is_file():
        _scan_file(root, report, max_file_bytes)
        return report
    deadline = time.monotonic() + time_budget
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(directory)
        for name in list(dirnames):
            if name in _SENSITIVE_DIRS:
                report.findings.append(Finding(str(here / name), "sensitive-dir", "credential directory"))
            if name in _SKIP_DIRS:
                dirnames.remove(name)
        if (here / ".git" / "config").is_file():
            _scan_file(here / ".git" / "config", report, max_file_bytes)
        for name in filenames:
            if (report.files_scanned >= max_files or report.bytes_scanned >= max_total_bytes
                    or time.monotonic() > deadline):
                report.truncated = True
                return report
            file_path = here / name
            if file_path.is_symlink() or not file_path.is_file():
                continue
            _scan_file(file_path, report, max_file_bytes)
    return report
