"""Real encrypted CLI, recorded model wire, and the pinned native Lean kernel."""

import json

import pytest

from hermes_platform.resolver import locate_command
from agent.formal_logic_lean import verify
from agent.formal_logic_schema import parse_checkpoint
from tests.e2e.core.mcp_plugins._helpers import build_home, run_chat_q
from tests.e2e.vault_home import connect_state
from tests.fakes.fake_llm_provider import FakeLLMServer, Text

pytestmark = pytest.mark.platforms("posix")


def _checkpoint(*, valid=True):
    return {"version": 1, "atoms": {"P": "P holds", "Q": "Q holds"},
            "assumptions": [
                {"id": "a", "statement": "P holds", "formula": "P", "kind": "given", "quote": "Assume P"},
                {"id": "b", "statement": "P implies Q", "formula": ["implies", "P", "Q"],
                 "kind": "given", "quote": "P implies Q"}],
            "hypotheses": [{"id": "h", "statement": "Q holds", "formula": "Q",
                            "from": ["a", "b"] if valid else ["a"]}],
            "conclusion": "h"}


def _answer(data):
    return "FORMAL_CONCLUSION: Q follows from the stated assumptions.\n\n```hermes_logic\n" + json.dumps(data) + "\n```"


def _lean():
    command = locate_command("lean").command
    assert command, "Install the pinned native Lean toolchain before running this qualification file"
    return command[0]


def _home(tmp_path, server, *, repairs=3):
    home = build_home(tmp_path, server.base_url, extra={
        "agent": {"max_turns": 6, "api_max_retries": 1,
                  "formal_logic": {"enabled": True, "lean_path": _lean(), "max_repairs": repairs}},
        "memory": {"memory_enabled": False, "user_profile_enabled": False},
        "auxiliary": {"title_generation": {"enabled": False}},
    })
    home.update_config(lambda cfg: cfg["model"].update(api_mode="chat_completions"))
    return home


def test_native_kernel_certifies_conditional_inference_and_rejects_inconsistency():
    data = _checkpoint()
    receipt = verify(parse_checkpoint(json.dumps(data)), _lean())
    assert receipt["status"] == "logic_verified" and receipt["axioms"] == []
    assert receipt["obligations"] == len(data["hypotheses"]) + 1
    data["assumptions"][1]["formula"] = ["not", "P"]
    assert verify(parse_checkpoint(json.dumps(data)), _lean())["error"] == "inconsistent_assumptions"


def test_real_cli_repairs_before_review_and_persists_only_in_encrypted_home(tmp_path):
    requests = []

    def main(record):
        requests.append(record["body"])
        return Text(_answer(_checkpoint(valid=len(requests) > 1)))

    def auxiliary(record):
        return Text(json.dumps({"alignment": "faithful", "reason": "The stated givens imply Q",
            "assumptions": [{"id": name, "verdict": "supported", "citations": [], "reason": "Explicit task stipulation"}
                            for name in ("a", "b")]}))

    with FakeLLMServer(main, aux=auxiliary) as server:
        home = _home(tmp_path, server)
        result = run_chat_q(home, "Assume P; P implies Q. Show that Q holds.", timeout=120)
        diagnostic = (result.returncode, result.stdout, result.stderr)
        assert result.returncode == 0 and "Formal check: Lean 4.35.0" in result.stdout, diagnostic
        assert len(requests) == 2, diagnostic
        assert len(server.aux_requests()) == 1, server.requests
        followup = requests[1]["messages"]
        assert any("invalid_deduction" in str(m.get("content")) for m in followup), followup
        assert requests[0]["messages"] == followup[:len(requests[0]["messages"])], "Prior prompt prefix was rewritten"
        assert home.config_path.read_bytes().startswith(b"HRMVAULT")
        with connect_state(home.hermes_home / "state.db") as database:
            saved = database.execute("SELECT content FROM messages WHERE role='assistant'").fetchall()
            assert any("Formal check: Lean 4.35.0" in str(row[0]) for row in saved)
        assert not (home.hermes_home / "state.db").read_bytes().startswith(b"SQLite format 3")


def test_real_cli_failure_budget_never_emits_unverified_conclusion_or_calls_reviewer(tmp_path):
    with FakeLLMServer(lambda record: Text(_answer(_checkpoint(valid=False)))) as server:
        home = _home(tmp_path, server, repairs=1)
        result = run_chat_q(home, "Assume P; P implies Q. Show Q.", timeout=120)
        assert "No verified conclusion is available" in result.stdout, (result.stdout, result.stderr)
        assert "FORMAL_CONCLUSION" not in result.stdout, result.stdout
        assert server.aux_requests() == []
        assert len(server.main_requests()) == 2


def test_real_owner_asks_stakeholder_only_after_logic_and_automatic_review(tmp_path):
    import sys
    from tests.e2e.vault_home import run_unlocked
    from tests.fakes.fake_llm_provider import ToolCall

    question = "Do you authorize proceeding with this plan?"
    data = {"version": 1, "atoms": {"P": "The operator authorizes proceeding with this plan"},
            "assumptions": [{"id": "a", "statement": "The operator authorizes proceeding with this plan",
                             "formula": "P", "kind": "stakeholder", "question": question}],
            "hypotheses": [{"id": "h", "statement": "Proceeding is authorized", "formula": "P", "from": ["a"]}],
            "conclusion": "h"}
    args = {"questions": [{"question": question, "choices": ["Accept assumption", "Reject assumption"]}]}
    seen = []

    def main(record):
        seen.append(record["body"])
        if len(seen) == 1:
            bad = json.loads(json.dumps(data))
            bad["hypotheses"][0]["formula"] = ["not", "P"]
            return ToolCall("clarify", args, text=_answer(bad))
        if len(seen) == 2:
            return ToolCall("clarify", args, text=_answer(data))
        if len(seen) == 3:
            return ToolCall("clarify", args)
        return Text(_answer(data))

    def auxiliary(record):
        return Text(json.dumps({"alignment": "faithful", "reason": "The operator owns this decision",
            "assumptions": [{"id": "a", "verdict": "stakeholder_required", "citations": [],
                             "reason": "Only the operator can authorize this plan"}]}))

    with FakeLLMServer(main, aux=auxiliary) as server:
        home = _home(tmp_path, server)
        program = """
import json,sys
from hermes_security.handoff import adopt_inherited_key
assert adopt_inherited_key()
from run_agent import AIAgent
calls=[]
def clarify(questions):
    calls.append(questions)
    return {"answers":{"q0":"Accept assumption"},"outcome":"submitted"}
agent=AIAgent(model="fake-model",provider="custom",api_key="fixture",
              base_url=sys.argv[1],api_mode="chat_completions",quiet_mode=True,
              skip_memory=True,skip_context_files=True,max_iterations=6,
              clarify_callback=clarify)
agent.skip_background_review=True
result=agent.run_conversation("Decide whether proceeding is authorized; obtain my decision when required.")
print("FORMAL_NATIVE_RECEIPT="+json.dumps({"calls":len(calls),"receipt":result.get("formal_logic"),
                                         "answer":result.get("final_response")}))
"""
        result = run_unlocked([sys.executable, "-c", program, server.base_url],
                              cwd=home.project, env=home.env(), capture_output=True, text=True, timeout=120)
        lines = [line for line in result.stdout.splitlines() if line.startswith("FORMAL_NATIVE_RECEIPT=")]
        assert result.returncode == 0 and lines, (result.stdout, result.stderr)
        receipt = json.loads(lines[-1].split("=", 1)[1])
        assert receipt["calls"] == 1 and receipt["receipt"]["accepted"] is True, receipt
        assert len(seen) == 4, seen
        assert any("invalid_deduction" in str(m.get("content")) for m in seen[1]["messages"])
        assert any("needs_stakeholder" in str(m.get("content")) for m in seen[2]["messages"])
        assert any("Accept assumption" in str(m.get("content")) for m in seen[3]["messages"])


def test_native_kernel_handles_full_fragment_and_irrelevant_declared_atoms():
    names = [f"P{i}" for i in range(10)]
    data = {"version": 1, "atoms": {p: p for p in names},
            "assumptions": [{"id": f"a{i}", "statement": p, "formula": p,
                             "kind": "given", "quote": p} for i, p in enumerate(names)],
            "hypotheses": [{"id": f"h{i}", "statement": p, "formula": p, "from": [f"a{i}"]}
                           for i, p in enumerate(names)], "conclusion": "h9"}
    assert verify(parse_checkpoint(json.dumps(data)), _lean())["status"] == "logic_verified"
    data["assumptions"] = []
    data["hypotheses"] = [{"id": "h", "statement": "P0 implies itself",
                           "formula": ["implies", "P0", "P0"], "from": []}]
    data["conclusion"] = "h"
    assert verify(parse_checkpoint(json.dumps(data)), _lean())["status"] == "logic_verified"


def test_real_cli_iteration_exhaustion_does_not_request_an_unchecked_summary(tmp_path):
    with FakeLLMServer(lambda record: Text(_answer(_checkpoint(valid=False)))) as server:
        home = _home(tmp_path, server)
        home.update_config(lambda cfg: cfg["agent"].update(max_turns=1))
        result = run_chat_q(home, "Assume P; P implies Q. Show Q.", timeout=120)
        assert "No verified conclusion is available" in result.stdout, (result.stdout, result.stderr)
        assert "FORMAL_CONCLUSION" not in result.stdout
        assert len(server.main_requests()) == 1 and not server.aux_requests()


def test_real_cli_intermediate_repair_budget_is_bounded_before_any_human_call(tmp_path):
    from tests.fakes.fake_llm_provider import ToolCall
    args = {"questions": [{"question": "Please approve my invalid proof", "choices": ["Yes", "No"]}]}
    with FakeLLMServer(lambda record: ToolCall("clarify", args, text=_answer(_checkpoint(valid=False)))) as server:
        home = _home(tmp_path, server, repairs=1)
        result = run_chat_q(home, "Assume P; P implies Q. Show Q.", timeout=120)
        assert "No verified conclusion is available" in result.stdout, (result.stdout, result.stderr)
        assert len(server.main_requests()) == 2 and not server.aux_requests()


def test_exit_zero_from_a_broken_checker_does_not_certify_a_proof(tmp_path):
    import sys
    from agent.formal_logic_lean import LEAN_COMMIT, LEAN_VERSION
    executable = tmp_path / "broken-lean"
    executable.write_text(f"#!{sys.executable}\nimport sys\n"
                          f"print('Lean (version {LEAN_VERSION}, commit {LEAN_COMMIT})' if '--version' in sys.argv else '')\n")
    executable.chmod(0o700)
    receipt = verify(parse_checkpoint(json.dumps(_checkpoint())), str(executable))
    assert receipt["status"] == "checker_failed"
    assert "false canary" in receipt["error"]
