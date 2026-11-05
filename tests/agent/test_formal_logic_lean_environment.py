"""Every compiler invocation uses the same empty cwd and credential-free environment."""
import json
from pathlib import Path

import pytest

from tests.agent._formal_checker import install_checker

from agent.formal_logic_lean import LEAN_COMMIT, LEAN_VERSION, verify
from agent.formal_logic_schema import parse_checkpoint


@pytest.mark.platforms("linux", "macos")
def test_checker_version_and_proof_calls_do_not_inherit_owner_authority(tmp_path, monkeypatch):
    record = tmp_path / "public-probe-metadata.jsonl"
    compiler = install_checker(tmp_path, monkeypatch, f"""
import json,os,sys
from pathlib import Path
with Path({str(record)!r}).open("a", encoding="utf-8") as output:
    output.write(json.dumps({{"arguments":sys.argv[1:],"cwd":os.getcwd(),
                            "grant":os.environ.get("HERMES_SESSION_VAULT"),
                            "key":os.environ.get("OPENAI_API_KEY")}})+"\\n")
if "--version" in sys.argv:
    print("Lean (version {LEAN_VERSION}, commit {LEAN_COMMIT})")
    raise SystemExit(0)
print(json.dumps({{"severity":"error","data":"deliberately unavailable test checker"}}))
raise SystemExit(1)
""")
    monkeypatch.setenv("HERMES_SESSION_VAULT", "synthetic-unused-owner-grant")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-unused-owner-key")
    checkpoint = parse_checkpoint(json.dumps({
        "version": 1, "atoms": {"P": "P holds"},
        "assumptions": [{"id": "a", "statement": "P holds", "formula": "P",
                         "kind": "given", "quote": "Assume P"}],
        "hypotheses": [{"id": "h", "statement": "P holds", "formula": "P", "from": ["a"]}],
        "conclusion": "h",
    }))
    result = verify(checkpoint, str(compiler))
    assert result["status"] != "logic_verified"
    observed = [json.loads(line) for line in record.read_text().splitlines()]
    assert len(observed) == 3
    assert all(item["grant"] is None and item["key"] is None for item in observed)
    assert len({item["cwd"] for item in observed}) == 1
    assert all(Path(item["cwd"]) != Path.cwd() for item in observed)
    assert not any(Path(item["cwd"]).exists() for item in observed)
