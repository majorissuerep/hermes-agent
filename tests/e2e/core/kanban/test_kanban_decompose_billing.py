"""Finite native Kanban decomposition: billed graph, dependency edges and fanout bounds.

No automatic dispatcher ticks or daemon retry policy exists. Every request below is an explicit
user-called ``hermes kanban decompose`` with real encrypted board state and a recording provider.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Callable

import pytest

from tests.e2e.core._pending_fixes import known_gate
from tests.e2e.core.kanban._helpers import Board
from tests.fakes.fake_llm_provider import FakeLLMServer, Response, Text

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX finite worker children")
KNOWN = {
    "test_huge_decompose_reply_is_bounded": (
        r"500-child reply created \d+ child rows \(bound \d+\)",
        "#118607 a 500-child decompose reply creates 500 child rows"),
}
MAX_CHILDREN = 64        # generous bound over the prompt's advisory 2-6 children
TASK_ID_RE = re.compile(r"Task id: (t_[0-9a-f]+)")


class UnboundedFanout(AssertionError):
    """A decompose reply fanned out past the child bound; the only type ``known_gate`` accepts here."""


# fake aux model ----------------------------------------------------------------------------------
Reply = Callable[[dict], Response]


def _single(_rec: dict) -> Text:
    return Text(json.dumps({"fanout": False, "rationale": "one unit", "title": "probe promoted",
                            "body": "do the one thing"}))


def _graph(n: int, edges: dict[int, list[int]] | None = None) -> Reply:
    tasks = [{"title": f"child {i}", "body": f"spec {i}", "assignee": None,
              "parents": (edges or {}).get(i, [])} for i in range(n)]
    payload = json.dumps({"fanout": True, "rationale": "split", "tasks": tasks})
    return lambda _rec: Text(payload, chunk_chars=4096)


class AuxModel:
    """Answers each decompose call by the task id in its prompt; records which card was billed."""

    def __init__(self) -> None:
        self.replies: dict[str, Reply] = {}

    @staticmethod
    def task_id(body: dict) -> str | None:
        m = TASK_ID_RE.search(json.dumps(body.get("messages", [])))
        return m.group(1) if m else None

    def __call__(self, rec: dict) -> Response:
        return self.replies.get(self.task_id(rec["body"]) or "", _single)(rec)


def billed_for(srv: FakeLLMServer, tid: str) -> int:
    return sum(1 for body in srv.aux_requests() if AuxModel.task_id(body) == tid)


# board readers -----------------------------------------------------------------------------------
def children_of(b: Board, root: str) -> list[str]:
    """Decomposed children: the root is linked under every child (task_links child_id == root)."""
    return [r["parent_id"] for r in b._q("SELECT parent_id FROM task_links WHERE child_id = ?", (root,))]


def task_count(b: Board) -> int:
    return b._q("SELECT COUNT(*) AS n FROM tasks")[0]["n"]


def test_valid_three_child_graph_bills_once_and_links_children(tmp_path: Path) -> None:
    """A finite request bills once and persists the exact declared graph."""
    model = AuxModel()
    with FakeLLMServer(aux=model) as srv:
        b = Board(tmp_path, srv.base_url)
        root = b.create("ship the feature", "--triage")
        model.replies[root] = _graph(3, edges={2: [0]})
        b.cli("decompose", root, "--json", timeout=240)
        kids = children_of(b, root)
        assert len(kids) == 3, b.diag(root)
        assert billed_for(srv, root) == 1, "a decomposed root must never be billed again"
        assert b.task(root)["status"] not in ("triage", "archived"), b.diag(root)
        assert task_count(b) == 1 + 3  # root and exactly the declared children
        rows = {b.task(k)["title"]: k for k in kids}
        assert sorted(rows) == ["child 0", "child 1", "child 2"]
        edge = b._q("SELECT 1 FROM task_links WHERE parent_id = ? AND child_id = ?",
                    (rows["child 0"], rows["child 2"]))
        assert edge, "declared dependency child 0 -> child 2 was not persisted"
        assert [e["payload"]["child_ids"] for e in b.events(root, "decomposed")] == [
            [rows[f"child {i}"] for i in range(3)]]


def test_huge_decompose_reply_is_bounded(tmp_path: Path) -> None:
    """One decompose call answering 500 children must be rejected or capped, not fanned out.
    Driven through the real ``hermes kanban decompose`` CLI (same decompose_task path)."""
    model = AuxModel()
    with FakeLLMServer(aux=model) as srv:
        b = Board(tmp_path, srv.base_url)
        root = b.create("tiny chore", "--triage")
        model.replies[root] = _graph(500)
        b.cli("decompose", root, "--json", timeout=240, check=False)
        assert billed_for(srv, root) == 1, "the decompose CLI must make exactly one aux call"
        kids = children_of(b, root)
        # A rejection leaves the root in triage with zero children; a cap leaves <= the bound.
        assert kids or b.task(root)["status"] == "triage", b.diag(root)
        assert task_count(b) == 1 + len(kids), "child rows exist that are not linked under the root"
        with known_gate(KNOWN, "test_huge_decompose_reply_is_bounded", raises=UnboundedFanout):
            if len(kids) > MAX_CHILDREN:
                raise UnboundedFanout(f"500-child reply created {len(kids)} child rows (bound {MAX_CHILDREN})")
