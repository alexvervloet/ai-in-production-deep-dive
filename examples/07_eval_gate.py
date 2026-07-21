#!/usr/bin/env python3
"""
07_eval_gate.py: a change ships only if it clears the bar.

    python examples/07_eval_gate.py            # offline, no key

This is where "measure it" (the evals repo) becomes "gate it." We score both
prompt versions against the same gold set (evals/gold.jsonl) and let the gate
decide which is allowed to ship. The gold set requires citations on at least one
case; v1 doesn't cite its sources, so it fails the gate, while the constrained v2
passes. In CI this exit code is what blocks a merge: a prompt that quietly drops
a required behavior can't reach production.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from prod import evals, prompts, providers

load_dotenv()


def answer_with(version: str):
    system = prompts.get_prompt(version)
    return lambda q: providers.generate(system, q).text


print(f"Scoring each prompt version against {len(evals.load_gold())} gold cases")
print("(threshold = 100%: every case must pass to ship)\n")

ship = []
for version in prompts.available_versions():
    report = evals.run_gate(answer_with(version), threshold=1.0)
    status = "PASS, eligible to ship" if report.ok else "FAIL, blocked"
    print(f"[{version}] {report.passed}/{report.total} cases  ->  {status}")
    for r in report.results:
        if not r.passed:
            print(f"       x {r.id}: {r.detail}")
    if report.ok:
        ship.append(version)

print(f"\nWould ship: {ship or '(nothing cleared the gate)'}")
print("\nThis is a normal test: the gate returns a pass/fail, CI turns it into an")
print("exit code, and a regression in the prompt or model can't reach production.")
sys.exit(0 if ship else 1)
