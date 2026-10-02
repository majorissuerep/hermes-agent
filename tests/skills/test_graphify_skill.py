"""Bundled Graphify: real project lifecycle, document cache, and scoped queries."""

import json
import os
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "software-development" / "graphify"


def test_project_graph_reuses_documents_and_refreshes_changed_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    monkeypatch.delenv("GRAPHIFY_OUT", raising=False)
    project = tmp_path / "project with spaces"
    project.mkdir()
    (project / "auth.py").write_text(
        "def validate_token(token):\n    return bool(token)\n\n"
        "def login(token):\n    return validate_token(token)\n", encoding="utf-8")
    document = project / "auth.md"
    document.write_text("# Token expiry\nTokens expire after ten minutes.\n", encoding="utf-8")
    (project / ".env").write_text("SECRET_KEY=should-not-be-indexed", encoding="utf-8")
    (project / ".graphifyignore").write_text("ignored.py\n", encoding="utf-8")
    (project / "ignored.py").write_text("def ignored_symbol(): pass\n", encoding="utf-8")

    def run(action, *args, success=True):
        result = subprocess.run(
            [sys.executable, str(SKILL / "scripts" / "project_graph.py"), action, str(project), *args],
            cwd=tmp_path, env=dict(os.environ), capture_output=True, text=True, timeout=60)
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        return result

    status = json.loads(run("scan").stdout)
    assert not status["graph_exists"]
    assert status["pending_documents"] == [document.name]
    assert ".env" not in status["changed_sources"]
    assert "ignored.py" not in status["changed_sources"]
    run("build")
    batch_path = project / "graphify-out" / "batch.json"
    batch = {
        "source_hashes": status["source_hashes"],
        "nodes": [{"id": "doc_token_expiry", "label": "Token expiry", "file_type": "document",
                   "source_file": document.name, "source_location": "lines 1-2"}],
        "edges": [], "hyperedges": [],
    }
    batch_path.write_text(json.dumps(batch), encoding="utf-8")
    built = json.loads(run("build", "--semantic", str(batch_path)).stdout)
    assert built["freshness"] == "fresh" and built["pending_document_count"] == 0
    assert "validate_token" in run("query", "validate_token").stdout
    assert "auth.md" in run("query", "Token expiry").stdout
    assert "validate_token" in run("path", "login", "validate_token").stdout
    assert "auth.py" in run("explain", "login").stdout
    graph_path = Path(built["graph"])
    graph_bytes = graph_path.read_bytes()
    # A new CLI process can rebuild entirely from Graphify's on-disk AST/semantic cache.
    run("build")
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    sources = {node.get("source_file") for node in graph["nodes"]}
    assert sources == {"auth.py", "auth.md"}
    assert not any(node["id"].startswith("ignored") for node in graph["nodes"])
    # Replacing the same document batch preserves its node identity.
    run("build", "--semantic", str(batch_path))
    document.write_text("# Token expiry\nTokens expire after twenty minutes now.\n", encoding="utf-8")
    changed = json.loads(run("scan").stdout)
    assert changed["freshness"] == "stale" and changed["pending_documents"] == [document.name]
    run("query", "Token expiry", success=False)
    run("build", "--semantic", str(batch_path), success=False)
    assert graph_path.read_bytes() == graph_bytes
    batch["source_hashes"] = changed["source_hashes"]
    batch["nodes"][0]["label"] = "Updated token expiry"
    batch_path.write_text(json.dumps(batch), encoding="utf-8")
    run("build", "--semantic", str(batch_path))
    assert "Updated token expiry" in run("query", "Token expiry").stdout
    # Intentional deletions shrink only after confirmation, and never leave stale doc nodes.
    document.unlink()
    run("build", success=False)
    refreshed = json.loads(run("build", "--force").stdout)
    assert refreshed["freshness"] == "fresh"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    assert all(node.get("source_file") != document.name for node in graph["nodes"])
    graph_bytes = graph_path.read_bytes()
    (project / "auth.py").unlink()
    run("build", "--force", success=False)
    assert graph_path.read_bytes() == graph_bytes
    # A document-only collection uses the same workflow without an external LLM backend.
    document.write_text("# Token expiry\nA standalone document collection.\n", encoding="utf-8")
    batch["source_hashes"] = json.loads(run("scan").stdout)["source_hashes"]
    batch_path.write_text(json.dumps(batch), encoding="utf-8")
    run("build", "--semantic", str(batch_path), "--force")
    assert "auth.md" in run("query", "Token expiry").stdout
    graph_bytes = graph_path.read_bytes()
    batch["source_hashes"] = {"../outside.md": "untrusted"}
    batch_path.write_text(json.dumps(batch), encoding="utf-8")
    run("build", "--semantic", str(batch_path), success=False)
    assert graph_path.read_bytes() == graph_bytes
    assert not (tmp_path / "user" / ".hermes").exists()
