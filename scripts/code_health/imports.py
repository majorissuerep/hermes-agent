"""During the pinned upstream sync, inherited debt is not newly authored debt.

The normal main comparison runs first. Only its reported occurrences are checked
against the reviewed upstream input too; a regression against both inputs still
blocks. Once main contains that input, the extra allowance disappears entirely.
"""

from pathlib import Path

from scripts.ci.upstream_sync_base import select
from scripts.code_health import gitio
from scripts.code_health.compare import compare
from scripts.code_health.measure import Measurer
from scripts.code_health.model import FileMeasure, Finding


def exclude_inherited(repo: Path, base: str, head: str | None, measurer: Measurer,
                      head_m: dict[str, FileMeasure], findings: list[Finding]) -> list[Finding]:
    tip = gitio.commit_or_none(repo, head) if head else None
    imported = select(base, tip or "HEAD", repo=repo)
    if imported == base or not findings:
        return findings
    paths = sorted({f.path for f in findings})
    prior = measurer.measure(imported, paths)
    candidate = {path: head_m[path] for path in paths}
    changes = [gitio.Change("M" if path in prior else "A",
                            path if path in prior else None, path) for path in paths]
    new = {(f.path, f.rule, f.scope, f.line) for f in compare(prior, candidate, changes)}
    return [f for f in findings if f.rule == "MEASURE" or (f.path, f.rule, f.scope, f.line) in new]
