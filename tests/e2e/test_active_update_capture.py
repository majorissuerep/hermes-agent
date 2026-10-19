"""Read real encrypted fixture receipts without exporting authority or conversation text."""
from argparse import Namespace
import hashlib
import json

from scripts.e2e.active_session_update import Acceptance


def test_durable_witness_reads_ciphertext_and_scrubs_fixture_authority(tmp_path, monkeypatch):
    from hermes_security.io import write_json
    from hermes_state import SessionDB
    from tests.e2e.vault_home import fixture_password, seal_fixture_home

    home = tmp_path / "home"
    home.mkdir()
    seal_fixture_home(home)
    monkeypatch.setenv("HERMES_HOME", str(home))
    password, api_key = fixture_password(home), "synthetic-private-api-credential"
    with SessionDB(home / "state.db") as database:
        database.create_session("parent", "cli")
        database.append_message("parent", "user", "Private conversation text must not be exported")
        database.append_message("parent", "assistant", tool_calls=[{"id": "call", "function": {
            "name": "terminal", "arguments": json.dumps({"command": "echo fixture"})}}])
        database.append_message("parent", "tool", json.dumps({"output": api_key + " " + password, "grants": "must-not-export-grants"}), tool_call_id="call")
    process_path = home / "logs/process-results/proc_owned.json"
    process_path.parent.mkdir(parents=True)
    write_json(process_path, {"id": "proc_owned", "parent_session_id": "parent", "output": api_key, "grants": "must-not-export-grants"}, purpose="state")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    acceptance = Acceptance.__new__(Acceptance)
    acceptance.home, acceptance.evidence = home, evidence
    acceptance.args = Namespace(baseline="a" * 40, candidate="b" * 40)
    acceptance.parent_id, acceptance.password = "parent", password
    acceptance.secret_values, acceptance.receipt = [api_key], {}
    monkeypatch.setattr(acceptance, "head", lambda: "b" * 40)
    acceptance.capture_proof()
    encoded = (evidence / "durable-tool-proof.json").read_bytes()
    witness = json.loads(encoded)
    assert api_key.encode() not in encoded and password.encode() not in encoded
    assert b"Private conversation text" not in encoded
    assert b"must-not-export-grants" not in encoded
    assert witness["source_head"] == "b" * 40
    assert [row["name"] for row in witness["calls"]] == ["terminal"]
    assert witness["processes"][0]["id"] == "proc_owned"
    assert acceptance.receipt["durable_tool_proof_sha256"] == hashlib.sha256(encoded).hexdigest()
    assert (home / "state.db").read_bytes()[:16] != b"SQLite format 3\0"
    assert api_key.encode() not in process_path.read_bytes()


def test_native_observer_records_only_three_real_hook_identities(tmp_path, monkeypatch):
    from hermes_cli.plugins import PluginManager
    from scripts.e2e.active_update_observer import install_observer, read_identities
    from tests.e2e.vault_home import seal_fixture_home

    home, evidence = tmp_path / "home", tmp_path / "evidence"
    home.mkdir()
    evidence.mkdir()
    seal_fixture_home(home)
    monkeypatch.setenv("HERMES_HOME", str(home))
    destination = install_observer(home, evidence)
    manager = PluginManager(scope_key=str(home))
    manager.discover_and_load()
    assert manager._hooks.get("subagent_start")
    manager.invoke_hook("subagent_start", parent_session_id="parent", child_session_id="child",
                        child_subagent_id="sa-owned", capabilities="never-export-this", child_goal="private goal")
    assert read_identities(destination) == [{"parent_session_id": "parent", "child_session_id": "child",
                                            "child_subagent_id": "sa-owned"}]
    assert destination.stat().st_mode & 0o777 == 0o600
