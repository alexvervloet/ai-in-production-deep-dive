#!/usr/bin/env python3
"""
10_model_fallback.py: failover and cost routing across models.

    python examples/10_model_fallback.py            # offline, no key

Reliability (Section 5) made *one* model call survive a blip with retries. This is
the next layer: when a model is down (or too expensive for the job), use a
*different* one. Two patterns:

  FAILOVER. If the primary model errors even after retries, fall back to a backup 
  a second provider, a cheaper model, or a safe canned answer. Better a slightly
  worse answer than a 500.

  COST ROUTING (cascade). Don't send every request to your biggest model. Route easy
  questions to a cheap, fast model and reserve the expensive one for the hard ones.
  Same quality where it matters, a fraction of the bill.

This runs offline on the mock provider. We simulate two "models" (a cheap one and a
strong one) and use `with_fallback` from the reliability layer for failover.

Run it:

    python examples/10_model_fallback.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from prod import providers
from prod.cost import price_of
from prod.reliability import with_fallback

load_dotenv()

# Two tiers with a real price gap (rates live in prod/cost.py): a cheap, fast model
# and a pricier, stronger one. The labels just pick a row in the price table.
CHEAP, STRONG = "gpt-4o-mini", "claude-haiku-4-5"


def call(model_label: str, question: str):
    """One model call, attributed to a tier (the mock answers either way)."""
    resp = providers.generate("You are the Acme Cloud support assistant.", question)
    cost = price_of(model_label, resp.prompt_tokens, resp.completion_tokens)
    return resp.text, cost


# --- 1. FAILOVER: the primary errors, fall back to a backup -------------------
def demo_failover():
    print("1) FAILOVER: primary model is down\n" + "-" * 40)
    # Force the next call to fail like a real outage would.
    providers.set_mock_behavior(fail_next=99)

    def primary():
        return call(STRONG, "How do I reset my password?")  # will raise

    def backup():
        providers.reset_mock_behavior()  # the backup model/provider is healthy
        text, cost = call(CHEAP, "How do I reset my password?")
        return f"[served by backup model {CHEAP}] {text}", cost

    text, cost = with_fallback(primary, backup)
    print(f"  primary ({STRONG}) failed -> fell back.")
    print(f"  answer: {text[:70]}...")
    print(f"  -> the user got an answer, not an error.\n")
    providers.reset_mock_behavior()


# --- 2. COST ROUTING: cheap model for easy, strong for hard -------------------
def is_hard(question: str) -> bool:
    """A trivial complexity heuristic. Real routers use length, a classifier, or a
    cheap model's own confidence."""
    return len(question.split()) > 12 or "why" in question.lower() or "compare" in question.lower()


def demo_routing():
    print("2) COST ROUTING: send each query to the right tier\n" + "-" * 40)
    questions = [
        "How do I reset my password?",
        "Can I get a refund?",
        "Why does my export fail when I include attachments and the file is over the size limit?",
        "Compare the Free and Plus plans for a small team and recommend one.",
    ]
    routed_cost = always_strong_cost = 0.0
    for q in questions:
        model = STRONG if is_hard(q) else CHEAP
        _, cost = call(model, q)
        routed_cost += cost
        _, strong_cost = call(STRONG, q)
        always_strong_cost += strong_cost
        print(f"  [{model:<11}] {q[:50]}")
    saved = (1 - routed_cost / always_strong_cost) * 100 if always_strong_cost else 0
    print(f"\n  routed cost:        ${routed_cost:.6f}")
    print(f"  always-strong cost: ${always_strong_cost:.6f}")
    print(f"  -> routing saved ~{saved:.0f}% by not over-serving easy questions.\n")


if __name__ == "__main__":
    print(f"Provider: {providers.describe()}\n")
    demo_failover()
    demo_routing()
    print(
        "Takeaway: a model is a dependency like any other. Have a backup for when it's\n"
        "down (failover) and don't pay top-tier prices for bottom-tier questions (cost\n"
        "routing). Both are a few lines around the same generate() call."
    )
