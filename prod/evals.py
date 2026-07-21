"""
prod/evals.py: the gate that decides whether a change ships.

The evals deep dive (repo #4) taught you to *measure* a change. This is where
that measurement gets teeth: a **gate**. Before a new prompt version, model, or
config goes live, it has to score above a threshold on a fixed gold dataset.
Below the bar, the change does not ship, the same idea as a failing test
blocking a merge.

The dataset lives in `evals/gold.jsonl`: each row is a question plus the
substrings the answer `expect`s (and any it `must_not` contain). The scorer here
is deliberately simple (substring checks) so it runs offline and deterministically
against the mock; in a real system this is exactly where you'd plug in the
LLM-as-judge scorer from the evals repo.

`run_gate()` takes *any* answer function, `answer_fn(question) -> str`, so you
can score the live app, or score a specific prompt version, without this file
depending on the app.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

_GOLD_PATH = Path(__file__).resolve().parent.parent / "evals" / "gold.jsonl"


def load_gold(path: Path | None = None) -> list[dict]:
    path = path or _GOLD_PATH
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


@dataclass
class CaseResult:
    id: str
    passed: bool
    detail: str


@dataclass
class GateReport:
    results: list[CaseResult] = field(default_factory=list)
    threshold: float = 1.0

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def ok(self) -> bool:
        """Did the change clear the gate?"""
        return self.pass_rate >= self.threshold


def score_case(answer: str, case: dict) -> CaseResult:
    answer_l = answer.lower()
    missing = [s for s in case.get("expect", []) if s.lower() not in answer_l]
    forbidden = [s for s in case.get("must_not", []) if s.lower() in answer_l]
    if missing:
        return CaseResult(case["id"], False, f"missing expected: {missing}")
    if forbidden:
        return CaseResult(case["id"], False, f"contained forbidden: {forbidden}")
    return CaseResult(case["id"], True, "ok")


def run_gate(answer_fn: Callable[[str], str], *, threshold: float = 1.0) -> GateReport:
    """Run `answer_fn` over the gold set and report whether it clears `threshold`."""
    report = GateReport(threshold=threshold)
    for case in load_gold():
        try:
            answer = answer_fn(case["question"])
        except Exception as exc:  # a crash is a failed case, not a crashed gate
            report.results.append(CaseResult(case["id"], False, f"error: {exc}"))
            continue
        report.results.append(score_case(answer, case))
    return report
