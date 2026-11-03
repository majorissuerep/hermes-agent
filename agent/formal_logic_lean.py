"""Deterministic Lean kernel checks over generated, axiom-free source."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile

from agent.formal_logic_schema import Checkpoint, counterexample
from hermes_platform.resolver import locate_command

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


def _environment() -> dict[str, str]:
    return {key: os.environ[key] for key in ("PATH", "SystemRoot") if key in os.environ}


def _invoke(executable: str, source: str, cwd: str) -> subprocess.CompletedProcess:
    # No shell, model-generated tactics, imports, plugins, files, or environment credentials.
    environment = _environment()
    return subprocess.run([executable, "--stdin", "--json", "-j1", "-M1024", "-t0"],
                          input=source, text=True, encoding="utf-8", capture_output=True,
                          cwd=cwd, env=environment, timeout=30, check=False)


def _messages(output: str) -> list[dict]:
    if len(output) > 64_000:
        raise ValueError("Lean output exceeded its bound")
    rows = [json.loads(line) for line in output.splitlines() if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("Invalid Lean diagnostic stream")
    return rows


def verify(checkpoint: Checkpoint, lean_path: str = "lean") -> dict:
    failure = counterexample(checkpoint)
    if failure is not None:
        return {"status": "logic_failed", **failure}
    command = locate_command(lean_path).command
    if not command:
        return {"status": "checker_unavailable", "error": "Pinned Lean 4.35.0 is unavailable"}
    executable = command[0]
    source, expected = lean_source(checkpoint)
    try:
        with tempfile.TemporaryDirectory(prefix="hermes-logic-") as cwd:
            version = subprocess.run([executable, "--version"], stdin=subprocess.DEVNULL,
                                     capture_output=True, text=True, encoding="utf-8", timeout=5,
                                     cwd=cwd, env=_environment(), check=False)
            if version.returncode or f"version {LEAN_VERSION}," not in version.stdout or LEAN_COMMIT not in version.stdout:
                return {"status": "checker_unavailable", "error": "Lean version/commit differs from the pinned toolchain"}
            canary = _invoke(executable, "import Init\nexample : False := by decide\n", cwd)
            canary_rows = _messages(canary.stdout)
            if canary.returncode == 0 or not any(r.get("severity") == "error" for r in canary_rows):
                return {"status": "checker_failed", "error": "The checker accepted or failed to diagnose the false canary"}
            result = _invoke(executable, source, cwd)
            rows = _messages(result.stdout)
        reports = {_THEOREM.fullmatch(r.get("data", "")).group(1) for r in rows
                   if _THEOREM.fullmatch(r.get("data", ""))}
        if result.returncode or any(r.get("severity") in {"error", "warning"} for r in rows) or reports != expected:
            return {"status": "logic_failed", "error": "Lean did not certify every axiom-free obligation",
                    "diagnostics": [r.get("data", "")[:1500] for r in rows][:20]}
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        return {"status": "checker_failed", "error": str(error)[:1500]}
    return {"status": "logic_verified", "checkpoint": checkpoint.digest, "lean": LEAN_VERSION,
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "obligations": len(expected), "axioms": []}
