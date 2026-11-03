"""Repository publication requires exact-head CI and preserves both histories."""
import http.server
import json
import os
from pathlib import Path
import subprocess
import threading
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts.ci import inhouse_status, publish_public


def test_only_latest_trusted_verification_and_native_application_qualify():
    valid = {"id": 1, "context": "ci/woodpecker/pr/verify", "status": "success",
             "creator": {"login": "hermes-ci"},
             "target_url": "https://ci.theincompetent.dev/repos/17/pipeline/1/1"}
    failed = dict(valid, id=2, status="failure")
    for untrusted in (dict(valid, id=3, creator={"login": "someone-else"}),
                      dict(valid, id=4, target_url="https://other.example/repos/17/pipeline/1/1"),
                      dict(valid, id=5, context="ci/woodpecker/pr/mirror")):
        assert inhouse_status.latest_verification([valid, failed, untrusted]) == failed
    check = {"id": 1, "name": "All required checks pass (v2)", "head_sha": "a" * 40,
             "app": {"id": 15368}, "status": "completed", "conclusion": "success"}
    assert inhouse_status.github_qualified([check], "a" * 40)
    for newer in (dict(check, id=2, conclusion="failure"), dict(check, id=3, status="in_progress")):
        assert not inhouse_status.github_qualified([check, newer], "a" * 40)
    assert not inhouse_status.github_qualified([dict(check, app={"id": 1})], "a" * 40)
    assert not inhouse_status.github_qualified([check], "b" * 40)


@pytest.mark.parametrize("latest_state", ["pending", "failure", "success"])
def test_verification_checks_later_history_pages_before_accepting_old_green(monkeypatch, latest_state):
    sha = "a" * 40
    status = {"context": "ci/woodpecker/pr/verify", "status": "success",
              "creator": {"login": "hermes-ci"},
              "target_url": "https://ci.theincompetent.dev/repos/17/pipeline/8/1"}
    rows = [dict(status, id=index) for index in range(1, 52)]
    rows[-1]["status"] = latest_state
    pages = []

    class History(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = parse_qs(urlsplit(self.path).query)
            page = int(query.get("page", ["1"])[0])
            pages.append(page)
            raw = json.dumps(rows[(page - 1) * 50:page * 50]).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), History)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(inhouse_status, "GITEA_API", f"http://127.0.0.1:{server.server_port}")
    try:
        if latest_state == "success":
            inhouse_status.wait_for_verification(sha, "fixture-read-only", timeout=0)
        else:
            with pytest.raises((RuntimeError, TimeoutError)):
                inhouse_status.wait_for_verification(sha, "fixture-read-only", timeout=0)
        assert pages == [1, 2]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("fault", [
    None, "source-moved", "wrong-application", "public-diverged",
    "private-failed", "private-pending", "private-missing", "private-regressed",
])
def test_real_git_mirror_advances_only_a_qualified_source_head(tmp_path, monkeypatch, fault):
    def git(directory: Path, *args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=directory, text=True,
                                       stderr=subprocess.PIPE, timeout=30).strip()

    source, public = tmp_path / "source", tmp_path / "public.git"
    source.mkdir()
    git(tmp_path, "init", "--bare", str(public))
    git(source, "init", "-b", "main")
    git(source, "config", "user.email", "fixture@example.invalid")
    git(source, "config", "user.name", "Fixture")
    (source / "content").write_text("base", encoding="utf-8")
    git(source, "add", ".")
    git(source, "-c", "core.hooksPath=/dev/null", "commit", "-m", "base")
    base = git(source, "rev-parse", "HEAD")
    git(source, "push", str(public), "HEAD:refs/heads/main")
    (source / "content").write_text("candidate", encoding="utf-8")
    git(source, "add", ".")
    git(source, "-c", "core.hooksPath=/dev/null", "commit", "-m", "candidate")
    sha = git(source, "rev-parse", "HEAD")
    if fault == "public-diverged":
        divergent = git(source, "commit-tree", "HEAD^{tree}", "-p", base, "-m", "other history")
        git(source, "push", str(public), divergent + ":refs/heads/main")
    before = git(public, "rev-parse", "refs/heads/main")
    requests = []

    class Provider(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization")))
            if self.path == "/source/branches/main":
                body = {"commit": {"id": base if fault == "source-moved" else sha}}
            elif self.path.startswith("/source/statuses/" + sha):
                state = {"private-failed": "failure", "private-pending": "pending"}.get(fault, "success")
                if fault == "private-regressed" and any(path.startswith("/public/") for path, _ in requests):
                    state = "pending"
                body = [] if fault == "private-missing" else [{
                    "id": 1, "context": "ci/woodpecker/push/verify", "status": state,
                    "creator": {"login": "hermes-ci"},
                    "target_url": "https://ci.theincompetent.dev/repos/17/pipeline/18/1",
                }]
            elif self.path.startswith("/public/commits/" + sha + "/check-runs"):
                body = {"check_runs": [{"id": 1, "name": "All required checks pass (v2)", "head_sha": sha,
                                       "app": {"id": 1 if fault == "wrong-application" else 15368},
                                       "status": "completed", "conclusion": "success"}]}
            else:
                self.send_error(404)
                return
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(publish_public, "ROOT", source)
    monkeypatch.setattr(publish_public, "PUBLIC_API", origin + "/public")
    monkeypatch.setattr(inhouse_status, "GITEA_API", origin + "/source")
    try:
        if fault:
            with pytest.raises((RuntimeError, TimeoutError, subprocess.CalledProcessError)):
                publish_public.publish(sha, dict(os.environ), "fixture-read-only", str(public), timeout=0)
            assert git(public, "rev-parse", "refs/heads/main") == before
            if fault in {"private-failed", "private-pending", "private-missing"}:
                assert not any(path.startswith("/public/") for path, _ in requests)
                absent = subprocess.run(
                    ["git", "-C", str(public), "show-ref", "--verify", "--quiet",
                     "refs/heads/mirror/qualification"], capture_output=True, timeout=30, check=False,
                )
                assert absent.returncode != 0
        else:
            publish_public.publish(sha, dict(os.environ), "fixture-read-only", str(public), timeout=0)
            assert git(public, "rev-parse", "refs/heads/main") == sha
            assert git(public, "rev-parse", "refs/heads/mirror/qualification") == sha
            assert any(path.startswith("/public/") and auth is None for path, auth in requests)
        assert any(path.startswith("/source/") and auth == "token fixture-read-only" for path, auth in requests)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
