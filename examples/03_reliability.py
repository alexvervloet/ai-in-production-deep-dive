#!/usr/bin/env python3
"""
03_reliability.py: make a flaky provider succeed anyway.

    python examples/03_reliability.py            # offline, no key

The mock can be told to fail on purpose, which gives the retry/backoff/fallback
code something real to handle. Three demos:

  1. Retry with backoff: fail twice, then succeed; watch the backoff grow.
  2. Fallback: fail every time; serve a safe canned answer instead of erroring.
  3. Circuit breaker: after enough failures, fail *fast* without even trying,
     to protect a struggling provider from a retry storm.
  4. Not every 429 is retryable: a "slow_down" waits out the server's
     Retry-After and recovers; a "credit_balance_exhausted" stops at once.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from prod import providers, reliability

load_dotenv()

SYSTEM = "You are the Acme Cloud support assistant."
Q = "How do I reset my password?"


print("1) Retry with exponential backoff (fail twice, then recover)")
providers.set_mock_behavior(fail_next=2)
resp = reliability.with_retry(
    lambda: providers.generate(SYSTEM, Q),
    base_delay=0.05,  # short so the demo is snappy
    on_retry=lambda n, e, d: print(f"   retry #{n} after {d:.2f}s: {e}"),
)
print(f"   -> succeeded: {resp.text[:48]}...\n")

print("2) Fallback (every attempt fails -> safe canned answer)")
providers.set_mock_behavior(fail_next=99)
result = reliability.with_fallback(
    lambda: reliability.with_retry(lambda: providers.generate(SYSTEM, Q), max_attempts=2, base_delay=0.02),
    lambda: "Sorry, I can't reach the help center right now. Email support@acme.example.",
)
print(f"   -> {result}\n")

print("3) Circuit breaker (opens after 3 failures, then fails fast)")
providers.set_mock_behavior(fail_next=99)
breaker = reliability.CircuitBreaker(fail_threshold=3, cooldown_s=10)
for i in range(1, 6):
    try:
        breaker.call(lambda: providers.generate(SYSTEM, Q))
    except Exception as exc:
        print(f"   call {i}: {breaker.state:9s} -> {type(exc).__name__}: {exc}")

print("\n4) Same status code, opposite answers (read the error code, not just the 429)")
import time  # noqa: E402

from prod.providers import PermanentProviderError  # noqa: E402

# OpenAI's slow_down: traffic ramped too fast. It can fire even under your rate
# limits, and the server says how long to wait.
providers.reset_mock_behavior()  # clear demo 3's failures
providers.set_mock_behavior(fail_with=[(429, "slow_down", 0.4)])
started = time.perf_counter()
resp = reliability.with_retry(
    lambda: providers.generate(SYSTEM, Q),
    base_delay=0.05,
    on_retry=lambda n, e, d: print(f"   retry #{n} after {d:.2f}s (Retry-After said 0.4s): {e}"),
)
print(f"   -> recovered in {time.perf_counter() - started:.2f}s\n")

# A 429 that is about money. Waiting can't fix it, so the retry layer doesn't try.
providers.set_mock_behavior(fail_with=[(429, "credit_balance_exhausted", None)])
attempts = {"n": 0}


def counted():
    attempts["n"] += 1
    return providers.generate(SYSTEM, Q)


try:
    reliability.with_retry(counted, base_delay=0.05)
except PermanentProviderError as exc:
    print(f"   -> gave up after {attempts['n']} attempt: {exc} (a person has to add credits)")

providers.reset_mock_behavior()
print("\nThe breaker stops hammering a sick provider; the fallback keeps users")
print("served. Together they turn provider outages into degraded, not broken, UX.")
