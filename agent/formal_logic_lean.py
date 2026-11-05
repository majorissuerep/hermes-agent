"""Deterministic Lean kernel checks over generated, axiom-free source."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time

from agent.formal_logic_schema import Checkpoint, counterexample
from agent.formal_logic_toolchain import installed_checker, workspace
from hermes_platform.checker_process import run as run_checker

LEAN_VERSION = "4.35.0"
LEAN_COMMIT = "eb6cafb7290e8bd5f2937522d8d804f58f02bafc"
_THEOREM = re.compile(r"'(hermes_[a-z0-9_]+)' does not depend on any axioms\Z")


def _lean_formula(formula, names: dict[str, str]) -> str:
    if isinstance(formula, bool):
        return "True" if formula else "False"
    if isinstance(formula, str):
        return f"({names[formula]} = true)"
    op, *args = formula
    parts = [_lean_formula(a, names) for a in args]
    if op == "not":
        return f"(Not {parts[0]})"
    symbol = {"and": "∧", "or": "∨", "implies": "→", "iff": "↔"}[op]
    return f"({parts[0]} {symbol} {parts[1]})"


def _atoms(formula) -> set[str]:
    if isinstance(formula, bool):
        return set()
    if isinstance(formula, str):
        return {formula}
    return set().union(*(_atoms(child) for child in formula[1:]))


def _quantified(quantifier: str, goal: str, formulas: list, names: dict[str, str]) -> str:
    used = set().union(*(_atoms(formula) for formula in formulas))
    variables = " ".join(value for key, value in names.items() if key in used)
    return f"{quantifier} ({variables} : Bool), {goal}" if variables else goal


def lean_source(checkpoint: Checkpoint) -> tuple[str, set[str]]:
    names = {name: f"x{i}" for i, name in enumerate(checkpoint.atoms)}
    context = [_lean_formula(a.formula, names) for a in checkpoint.assumptions]
    conjunction = " ∧ ".join(context) if context else "True"
    consistency = _quantified("∃", conjunction, [a.formula for a in checkpoint.assumptions], names)
    lines = ["import Init", "set_option maxRecDepth 8192", "set_option maxHeartbeats 2000000",
             "set_option synthInstance.maxSize 100000",
             f"theorem hermes_consistent : {consistency} := by decide"]
    expected = {"hermes_consistent"}
    formulas = {a.id: a.formula for a in checkpoint.assumptions}
    for index, hypothesis in enumerate(checkpoint.hypotheses):
        identifier = f"hermes_step_{index}"
        goal = " → ".join([*(_lean_formula(formulas[p], names) for p in hypothesis.premises),
                           _lean_formula(hypothesis.formula, names)])
        quantified = _quantified("∀", goal, [*(formulas[p] for p in hypothesis.premises), hypothesis.formula], names)
        lines.append(f"theorem {identifier} : {quantified} := by decide")
        expected.add(identifier)
        formulas[hypothesis.id] = hypothesis.formula
    lines.extend(f"#print axioms {name}" for name in sorted(expected))
    return "\n".join(lines) + "\n", expected


def _invoke(selected, arguments, source, cwd, attempt, *, timeout=30):
    attempt["invocations"] += 1
    return run_checker(selected.executable, arguments, directory=cwd, source=source, timeout=timeout)


def _messages(output: str) -> list[dict]:
    if len(output) > 64_000:
        raise ValueError("Lean output exceeded its bound")
    rows = [json.loads(line) for line in output.splitlines() if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("Invalid Lean diagnostic stream")
    return rows


def verify(checkpoint: Checkpoint, lean_path: str = "lean") -> dict:
    started = time.monotonic()
    attempt = {"invocations": 0, "maximum_invocations": 3,
               "process_wall_budget_seconds": 65, "toolchain": None, "workspace": "not_created"}
    try:
        result = _verify(checkpoint, lean_path, attempt)
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as error:
        result = {"status": "checker_failed", "proof_status": "unknown", "error": str(error)[:1500]}
    return {**result, "checker": attempt, "elapsed_seconds": time.monotonic() - started}


def _verify(checkpoint, lean_path, attempt):
    failure = counterexample(checkpoint)
    if failure is not None:
        return {"status": "logic_failed", "proof_status": "not_certified", **failure}
    selected = installed_checker(lean_path, version=LEAN_VERSION)
    if selected is None:
        return {"status": "checker_unavailable", "proof_status": "unknown",
                "error": "Verified PM Lean 4.35.0 is unavailable. Run hermes pm install lean --tools-only; "
                         "an explicit lean_path must match that selected PM binary."}
    attempt["toolchain"] = selected.receipt()
    source, expected = lean_source(checkpoint)
    arguments = ["--stdin", "--json", "-j1", "-M1024", "-t0"]
    with workspace() as (cwd, posture):
        attempt["workspace"] = posture
        version = _invoke(selected, ["--version"], None, cwd, attempt, timeout=5)
        if version.returncode or f"version {LEAN_VERSION}," not in version.stdout or LEAN_COMMIT not in version.stdout:
            return {"status": "checker_unavailable", "error": "Lean version/commit differs from the pinned toolchain"}
        canary = _invoke(selected, arguments, "import Init\nexample : False := by decide\n", cwd, attempt)
        canary_rows = _messages(canary.stdout)
        if canary.returncode == 0 or not any(r.get("severity") == "error" for r in canary_rows):
            return {"status": "checker_failed", "error": "The checker accepted or failed to diagnose the false canary"}
        result = _invoke(selected, arguments, source, cwd, attempt)
        rows = _messages(result.stdout)
    reports = {_THEOREM.fullmatch(r.get("data", "")).group(1) for r in rows
               if _THEOREM.fullmatch(r.get("data", ""))}
    if result.returncode or any(r.get("severity") in {"error", "warning"} for r in rows) or reports != expected:
        return {"status": "logic_failed", "error": "Lean did not certify every axiom-free obligation",
                "diagnostics": [r.get("data", "")[:1500] for r in rows][:20]}
    return {"status": "logic_verified", "proof_status": "verified_finite_conditional_deductions",
            "premise_truth": "not_checked_by_kernel", "checkpoint": checkpoint.digest, "lean": LEAN_VERSION,
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "obligations": len(expected), "axioms": []}
