#!/usr/bin/env python3
"""
11_rate_limiting_and_feedback.py: protect the service, then learn from it.

    python examples/11_rate_limiting_and_feedback.py            # offline, no key

Two production concerns that bracket a request: one guards the door on the way in,
the other learns from what happened on the way out.

  RATE LIMITING / QUOTAS. A shared LLM backend is expensive and has finite capacity.
  Without limits, one buggy client or one heavy tenant starves everyone else (and
  runs up your bill). A per-tenant **token bucket** caps the rate fairly: each
  tenant gets a steady refill and a small burst, and excess requests are throttled
  (a 429), not served.

  THE FEEDBACK FLYWHEEL. Production is the best source of eval data you'll ever have.
  Capture a 👍/👎 on answers, and your thumbs-down cases become a labelled set of
  exactly the things your system gets wrong. Feed them into the evals dive (#5) as
  regression tests and as fine-tuning data. Real usage → better system → repeat.

Both run fully offline.

Run it:

    python examples/11_rate_limiting_and_feedback.py
"""

import os
import sys
import time
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from prod import providers

load_dotenv()


# --- 1. Per-tenant token-bucket rate limiter ---------------------------------
@dataclass
class TokenBucket:
    """`capacity` tokens, refilling at `refill_per_sec`. Each request costs 1 token;
    if the bucket is empty, the request is throttled."""

    capacity: float
    refill_per_sec: float
    tokens: float = field(default=None)  # type: ignore
    last: float = field(default_factory=time.monotonic)

    def __post_init__(self):
        if self.tokens is None:
            self.tokens = self.capacity

    def allow(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.refill_per_sec)
        self.last = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


class RateLimiter:
    """One bucket per tenant: the heart of multi-tenant fairness."""

    def __init__(self, capacity=5, refill_per_sec=2.0):
        self.capacity, self.refill = capacity, refill_per_sec
        self.buckets: dict[str, TokenBucket] = {}

    def allow(self, tenant: str) -> bool:
        b = self.buckets.setdefault(tenant, TokenBucket(self.capacity, self.refill))
        return b.allow()


# --- 2. The feedback flywheel ------------------------------------------------
@dataclass
class FeedbackLog:
    """Records (question, answer, 👍/👎). The 👎 rows are your next eval set."""

    rows: list[dict] = field(default_factory=list)

    def record(self, question: str, answer: str, rating: str) -> None:
        self.rows.append({"input": question, "output": answer, "rating": rating})

    def thumbs_down(self) -> list[dict]:
        return [r for r in self.rows if r["rating"] == "down"]


def demo_rate_limiting():
    print("1) RATE LIMITING: one tenant's burst can't starve the others\n" + "-" * 40)
    limiter = RateLimiter(capacity=5, refill_per_sec=2.0)
    # Tenant A floods with 8 rapid requests; tenant B sends 2.
    print("  tenant A sends 8 requests in a burst (capacity 5):")
    allowed = sum(limiter.allow("tenant-A") for _ in range(8))
    print(f"    {allowed} served, {8 - allowed} throttled (429); the burst is capped.")
    print("  tenant B sends 2 requests (its own bucket is full):")
    allowed_b = sum(limiter.allow("tenant-B") for _ in range(2))
    print(f"    {allowed_b}/2 served; A's flood didn't affect B. That's fairness.\n")


def demo_feedback():
    print("2) FEEDBACK FLYWHEEL: turn production into eval data\n" + "-" * 40)
    log = FeedbackLog()
    interactions = [
        ("How do I reset my password?", "up"),
        ("Can I get a refund?", "up"),
        ("Do you support quantum entanglement?", "down"),   # KB miss -> bad answer
        ("What's the SLA for the Free plan?", "down"),
    ]
    for question, rating in interactions:
        resp = providers.generate("You are the Acme Cloud support assistant.", question)
        log.record(question, resp.text, rating)
        print(f"  {'👍' if rating == 'up' else '👎'}  {question}")

    bad = log.thumbs_down()
    print(f"\n  {len(bad)} thumbs-down captured -> export as an eval set (eval_set.jsonl):")
    for r in bad:
        print(f'    {{"input": {r["input"]!r}, "expected": "<fix me>"}}')
    print("  -> These are exactly the cases to add as regression tests (evals dive) and\n"
          "     candidate fine-tuning data. Production tells you what to fix next.\n")


if __name__ == "__main__":
    print(f"Provider: {providers.describe()}\n")
    demo_rate_limiting()
    demo_feedback()
    print(
        "Takeaway: rate limiting protects a shared, costly backend from any one client\n"
        "(fairness + cost control + multi-tenancy); the feedback flywheel turns real\n"
        "usage into the labelled data that makes the system better over time. Bracket\n"
        "every request: guard the input, learn from the output."
    )
