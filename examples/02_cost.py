#!/usr/bin/env python3
"""
02_cost.py: turn tokens into dollars, and refuse to overspend.

    python examples/02_cost.py            # offline, no key

Two jobs the teaching repos didn't have: *attribute* every call's cost, and
*enforce* a ceiling. We set a tiny budget, then keep asking questions until the
next call would blow past it, and watch the budget refuse it instead of
spending. That refusal is the difference between a bounded bill and a surprise
invoice from a runaway loop.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from prod import cost, providers

load_dotenv()

SYSTEM = "You are the Acme Cloud support assistant. Always cite the source."
budget = cost.Budget(limit_usd=0.0002)  # deliberately tiny so we hit it fast

questions = [
    "How do I reset my password?",
    "Can I get a refund?",
    "How do I cancel my subscription?",
    "How do I export my data?",
    "What plans do you offer?",
]

print(f"Budget: ${budget.limit_usd:.4f}   (provider: {providers.provider_name()})\n")
for q in questions:
    resp = providers.generate(SYSTEM, q)
    usd = cost.price_of(resp.model, resp.prompt_tokens, resp.completion_tokens)
    try:
        budget.check(usd)
    except cost.BudgetExceeded as exc:
        print(f"BLOCKED  {q!r}")
        print(f"         -> {exc}")
        break
    budget.record(resp.model, usd)
    print(f"ok  ${usd:.6f}  (spent ${budget.spent_usd:.6f} / ${budget.limit_usd:.4f})  {q!r}")

print(f"\nTotal spent: ${budget.spent_usd:.6f} across {budget.calls} calls")
print(f"By model: {budget.by_model}")
print("\nReal systems keep one budget per customer/endpoint/day. The mechanic is the")
print("same: check() before you spend, record() after, stop at the ceiling.")
