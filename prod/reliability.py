"""
prod/reliability.py: survive a provider that fails, slows, or rate-limits.

The teaching repos had a section on retries; here it becomes a layer the app
always goes through. Real model APIs return 429s under load, 503s during
incidents, and occasionally just hang. The job of this layer is to turn those
*transient* failures into a successful answer when possible, and a clean,
fast failure when not.

Three classic patterns, from scratch:

  1. **Retry with exponential backoff + jitter**: wait 0.5s, then 1s, then 2s
     (each with a little randomness so a thousand clients don't retry in lockstep
     and hammer a recovering server). Only retry errors that are actually
     transient.
  2. **Fallback**: if the primary path keeps failing, switch to a cheaper/older
     model or a canned safe answer rather than showing the user an error.
  3. **Circuit breaker**: after repeated failures, stop calling for a cooldown
     window. This protects a struggling provider from a retry storm and fails
     fast instead of making every user wait through the full backoff.

These wrap *any* callable, so the same code protects a model call, an embedding
call, or a database query.
"""

from __future__ import annotations

import random
import time
from typing import Callable, Iterable, TypeVar

from prod.providers import TransientProviderError

T = TypeVar("T")

# Which exceptions are worth retrying. A 400 "bad request" is your bug: retrying
# just wastes time. A 503 is the server's problem: retrying often works.
RETRYABLE = (TransientProviderError, TimeoutError, ConnectionError)


def with_retry(
    fn: Callable[[], T],
    *,
    max_attempts: int = 4,
    base_delay: float = 0.5,
    retryable: Iterable[type[BaseException]] = RETRYABLE,
    on_retry: Callable[[int, BaseException, float], None] | None = None,
) -> T:
    """Call `fn`, retrying transient failures with exponential backoff + jitter.

    Raises the last error if every attempt fails. `on_retry(attempt, error, delay)`
    is a hook so the caller can log each retry into its trace.
    """
    retryable = tuple(retryable)
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except retryable as exc:
            last_exc = exc
            if attempt == max_attempts:
                break
            # Exponential backoff: base * 2^(attempt-1), plus up to 50% jitter.
            delay = base_delay * (2 ** (attempt - 1))
            delay += random.uniform(0, delay * 0.5)
            if on_retry:
                on_retry(attempt, exc, delay)
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def with_fallback(primary: Callable[[], T], fallback: Callable[[], T]) -> T:
    """Try `primary`; on *any* error, fall back to `fallback`.

    Use this for the last line of defense: a cheaper model, a cached/stale answer,
    or a safe canned message: anything better than a 500 to the user.
    """
    try:
        return primary()
    except Exception:
        return fallback()


class CircuitBreaker:
    """Stop calling a failing dependency for a cooldown, then test the water.

    States: closed (calls flow), open (calls fail fast), half-open (one trial
    call decides whether to close again). This is the pattern that keeps a single
    sick provider from taking your whole app down with backoff latency.
    """

    def __init__(self, *, fail_threshold: int = 3, cooldown_s: float = 5.0):
        self.fail_threshold = fail_threshold
        self.cooldown_s = cooldown_s
        self.failures = 0
        self.opened_at: float | None = None

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        if time.monotonic() - self.opened_at >= self.cooldown_s:
            return "half-open"
        return "open"

    def call(self, fn: Callable[[], T]) -> T:
        if self.state == "open":
            raise TransientProviderError("circuit open, failing fast (provider is unhealthy)")
        try:
            result = fn()
        except Exception:
            self.failures += 1
            if self.failures >= self.fail_threshold:
                self.opened_at = time.monotonic()
            raise
        else:
            # Success closes the circuit and clears the failure count.
            self.failures = 0
            self.opened_at = None
            return result
