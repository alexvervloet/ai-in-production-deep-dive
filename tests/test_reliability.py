"""
Retry decisions: what gets retried, how long it waits, and what gives up at once.

Offline. The SDK exceptions are the SDKs' own classes, built around a minimal stand-in
response, so these test the same objects a real 429 or 529 produces, without a key
or a network call.

Run it:  python -m unittest discover -s tests
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prod import providers, reliability  # noqa: E402
from prod.providers import (  # noqa: E402
    PermanentProviderError,
    TransientProviderError,
    classify_error,
)


class _Response:
    """What the SDK error constructors read from a response, and nothing else.

    A stand-in rather than a real HTTP response, because openai 3.x and anthropic
    1.x use httpx2 internally and a test shouldn't depend on which HTTP library
    the SDK happens to ship with this month."""

    def __init__(self, status, headers=None):
        self.status_code = status
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.request = None


def _response(status, headers=None):
    return _Response(status, headers)


class TestClassify(unittest.TestCase):
    def test_slow_down_is_transient_and_keeps_retry_after(self):
        err = classify_error(429, "slow_down", "ramping too fast", 2.0)
        self.assertIsInstance(err, TransientProviderError)
        self.assertEqual(err.retry_after, 2.0)

    def test_billing_429s_are_permanent(self):
        for code in providers.BILLING_CODES:
            with self.subTest(code=code):
                self.assertIsInstance(classify_error(429, code, "x"), PermanentProviderError)

    def test_overload_and_server_errors_are_transient(self):
        for status, code in [(503, "server_is_overloaded"), (500, None), (529, "overloaded_error")]:
            with self.subTest(status=status):
                self.assertIsInstance(classify_error(status, code, "x"), TransientProviderError)

    def test_client_errors_are_permanent(self):
        for status in (400, 401, 403, 404):
            with self.subTest(status=status):
                self.assertIsInstance(classify_error(status, None, "x"), PermanentProviderError)


class TestFromSdk(unittest.TestCase):
    def test_openai_slow_down_with_retry_after(self):
        import openai

        exc = openai.RateLimitError(
            "slow down", response=_response(429, {"retry-after": "3"}),
            body={"code": "slow_down", "type": "rate_limit_error"},
        )
        err = providers._from_sdk_error(exc)
        self.assertIsInstance(err, TransientProviderError)
        self.assertEqual((err.code, err.retry_after), ("slow_down", 3.0))

    def test_openai_credit_balance_exhausted_is_permanent(self):
        import openai

        exc = openai.RateLimitError(
            "no credits", response=_response(429), body={"code": "credit_balance_exhausted"}
        )
        self.assertIsInstance(providers._from_sdk_error(exc), PermanentProviderError)

    def test_anthropic_overloaded_529(self):
        import anthropic

        exc = anthropic.APIStatusError(
            "overloaded", response=_response(529),
            body={"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
        )
        err = providers._from_sdk_error(exc)
        self.assertIsInstance(err, TransientProviderError)
        self.assertEqual(err.code, "overloaded_error")


class TestWithRetry(unittest.TestCase):
    def test_waits_at_least_retry_after(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise TransientProviderError("slow down", code="slow_down", retry_after=5.0)
            return "ok"

        with mock.patch.object(reliability.time, "sleep") as sleep:
            self.assertEqual(reliability.with_retry(flaky, base_delay=0.01), "ok")
        self.assertGreaterEqual(sleep.call_args.args[0], 5.0)

    def test_permanent_error_is_not_retried(self):
        calls = {"n": 0}

        def broke():
            calls["n"] += 1
            raise PermanentProviderError("no credits", code="credit_balance_exhausted")

        with mock.patch.object(reliability.time, "sleep"):
            with self.assertRaises(PermanentProviderError):
                reliability.with_retry(broke)
        self.assertEqual(calls["n"], 1)


if __name__ == "__main__":
    unittest.main()
