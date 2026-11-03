"""Bounded propositional checkpoints; model input is data, never Lean source."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import itertools
import json
import re
from typing import Any

MAX_BYTES = 48_000
MAX_ATOMS = 10
MAX_STEPS = 16
_NAME = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{0,31}\Z")
_ARITIES = {"not": 1, "and": 2, "or": 2, "implies": 2, "iff": 2}


class LogicFormatError(ValueError):
    """The checkpoint is outside the experimental language."""


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise LogicFormatError(f"{label} must be nonempty text, at most 2000 characters")
    return value.strip()


def _object(value: Any, allowed: set[str], required: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) - allowed or required - set(value):
        raise LogicFormatError(f"Expected object keys {sorted(required)}; allowed {sorted(allowed)}")
    return value


def _formula(value: Any, atoms: dict, depth: int = 0, budget: list[int] | None = None):
    budget = [256] if budget is None else budget
    budget[0] -= 1
    if depth > 12 or budget[0] < 0:
        raise LogicFormatError("Formula exceeds the depth/node bound")
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value in atoms:
        return value
    if isinstance(value, list) and value and isinstance(value[0], str):
        op = value[0]
        if op in _ARITIES and len(value) == _ARITIES[op] + 1:
            return (op, *(_formula(v, atoms, depth + 1, budget) for v in value[1:]))
    raise LogicFormatError("Use atom names, booleans, or [not/and/or/implies/iff, ...]")


@dataclass(frozen=True)
class Assumption:
    id: str
    statement: str
    formula: Any
    kind: str
    query: str = ""
    question: str = ""
    quote: str = ""


@dataclass(frozen=True)
class Hypothesis:
    id: str
    statement: str
    formula: Any
    premises: tuple[str, ...]


@dataclass(frozen=True)
class Checkpoint:
    atoms: dict[str, str]
    assumptions: tuple[Assumption, ...]
    hypotheses: tuple[Hypothesis, ...]
    conclusion: str
    digest: str


def _name(raw: Any, seen: set[str]) -> str:
    if not isinstance(raw, str) or not _NAME.fullmatch(raw) or raw in seen:
        raise LogicFormatError("Identifiers must be unique short ASCII names")
    seen.add(raw)
    return raw


def parse_checkpoint(raw: str) -> Checkpoint:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_BYTES:
        raise LogicFormatError("Checkpoint exceeds its 48 KB bound")
    try:
        data = json.loads(raw, object_pairs_hook=_unique_keys)
    except (ValueError, RecursionError) as error:
        raise LogicFormatError(f"Invalid checkpoint JSON: {error}") from error
    _object(data, {"version", "atoms", "assumptions", "hypotheses", "conclusion"},
            {"version", "atoms", "assumptions", "hypotheses", "conclusion"})
    if type(data["version"]) is not int or data["version"] != 1:
        raise LogicFormatError("Only checkpoint version 1 is supported")
    atoms = data["atoms"]
    if not isinstance(atoms, dict) or not 1 <= len(atoms) <= MAX_ATOMS:
        raise LogicFormatError(f"Declare between 1 and {MAX_ATOMS} atoms")
    seen: set[str] = set()
    for key, meaning in atoms.items():
        _name(key, seen)
        _text(meaning, "atom meaning")
    assumptions = _parse_assumptions(data["assumptions"], atoms)
    hypotheses = _parse_hypotheses(data["hypotheses"], atoms, assumptions)
    if not isinstance(data["conclusion"], str) or data["conclusion"] not in {h.id for h in hypotheses}:
        raise LogicFormatError("Conclusion must name a proved hypothesis")
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return Checkpoint(atoms, assumptions, hypotheses, data["conclusion"],
                      hashlib.sha256(canonical.encode()).hexdigest())


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise LogicFormatError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_assumptions(rows: Any, atoms: dict) -> tuple[Assumption, ...]:
    if not isinstance(rows, list) or len(rows) > MAX_STEPS:
        raise LogicFormatError(f"At most {MAX_STEPS} assumptions are allowed")
    result, seen = [], set(atoms)
    fields = {"id", "statement", "formula", "kind", "query", "question", "quote"}
    for row in rows:
        _object(row, fields, {"id", "statement", "formula", "kind"})
        kind = row["kind"]
        if not isinstance(kind, str):
            raise LogicFormatError("Assumption kind must be text")
        required = {"fact": "query", "stakeholder": "question", "given": "quote"}.get(kind)
        if required is None:
            raise LogicFormatError("Assumption kind is fact, stakeholder, or given")
        for key in ("query", "question", "quote"):
            if key != required and key in row:
                raise LogicFormatError(f"{kind} assumptions use only {required}")
        extra = {required: _text(row.get(required), required)}
        result.append(Assumption(_name(row["id"], seen), _text(row["statement"], "statement"),
                                 _formula(row["formula"], atoms), kind, **extra))
    return tuple(result)


def _parse_hypotheses(rows: Any, atoms: dict, assumptions: tuple) -> tuple[Hypothesis, ...]:
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_STEPS:
        raise LogicFormatError(f"Declare between 1 and {MAX_STEPS} hypotheses")
    seen = set(atoms) | {a.id for a in assumptions}
    available = {a.id for a in assumptions}
    result = []
    for row in rows:
        _object(row, {"id", "statement", "formula", "from"}, {"id", "statement", "formula", "from"})
        premises = row["from"]
        if (not isinstance(premises, list) or len(premises) > 32
                or not all(isinstance(p, str) and p in available for p in premises)
                or len(set(premises)) != len(premises)):
            raise LogicFormatError("from must name distinct assumptions or earlier proved hypotheses")
        identifier = _name(row["id"], seen)
        result.append(Hypothesis(identifier, _text(row["statement"], "statement"),
                                 _formula(row["formula"], atoms), tuple(premises)))
        available.add(identifier)
    return tuple(result)


def evaluate(formula, assignment: dict[str, bool]) -> bool:
    if isinstance(formula, bool):
        return formula
    if isinstance(formula, str):
        return assignment[formula]
    op, *args = formula
    values = [evaluate(arg, assignment) for arg in args]
    if op == "not":
        return not values[0]
    if op == "and":
        return all(values)
    if op == "or":
        return any(values)
    if op == "implies":
        return not values[0] or values[1]
    return values[0] == values[1]


def counterexample(checkpoint: Checkpoint) -> dict | None:
    """Reject contradictions and return an explicit failed deduction before compiling."""
    formulas = {a.id: a.formula for a in checkpoint.assumptions}
    assignments = [dict(zip(checkpoint.atoms, values)) for values in
                   itertools.product((False, True), repeat=len(checkpoint.atoms))]
    viable = [v for v in assignments if all(evaluate(a.formula, v) for a in checkpoint.assumptions)]
    if not viable:
        return {"error": "inconsistent_assumptions", "message": "No valuation satisfies all assumptions"}
    for hypothesis in checkpoint.hypotheses:
        for valuation in assignments:
            if (all(evaluate(formulas[p], valuation) for p in hypothesis.premises)
                    and not evaluate(hypothesis.formula, valuation)):
                return {"error": "invalid_deduction", "hypothesis": hypothesis.id,
                        "counterexample": valuation}
        formulas[hypothesis.id] = hypothesis.formula
    return None
