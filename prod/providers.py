"""
prod/providers.py — the ONLY file that talks to a model provider.
=================================================================

Same keystone idea as every sibling repo: hide the one provider-specific call
behind a tiny function so the rest of the code is provider-agnostic. Here that
matters even more, because the whole point of this repo is the *machinery around*
the model call — observability, cost, retries, caching, guardrails, prompt
versioning, eval gates. None of that machinery should care who serves the model.

What's new here is a third stack: **`mock`**, the default.

  PROVIDER=mock   ->  a deterministic, offline, in-process model. No key, no
                      network, no cost. It answers from a tiny built-in support
                      knowledge base so the ops machinery has something real to
                      wrap. This is what makes the entire repo runnable with no
                      key.
  PROVIDER=openai ->  OpenAI chat                 (needs OPENAI_API_KEY)
  PROVIDER=claude ->  Claude messages             (needs ANTHROPIC_API_KEY)

Every layer in `prod/` calls `generate()` and gets back an `LLMResponse` — text
plus the metadata production code actually needs: token counts, the model name,
and how long the call took. Real providers fill those from the API response; the
mock computes them locally so cost and latency dashboards work offline.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from functools import lru_cache

# Default models per stack. The mock's "model name" is cosmetic — it only shows
# up in logs and cost reports so they look like the real thing.
_OPENAI_CHAT = "gpt-4o-mini"
_CLAUDE_CHAT = "claude-haiku-4-5"
_MOCK_MODEL = "mock-1"

_KEYS = {
    "mock": [],  # the whole point: no key required
    "openai": ["OPENAI_API_KEY"],
    "claude": ["ANTHROPIC_API_KEY"],
}


@dataclass
class LLMResponse:
    """One model call's result, plus the metadata the ops layers need.

    `text`       — the answer.
    `model`      — which model produced it (for logs / cost attribution).
    `prompt_tokens` / `completion_tokens` — for cost accounting (Section 3).
    `latency_ms` — wall-clock time of the call (for observability, Section 1).
    """

    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def provider_name() -> str:
    """The active stack: 'mock' (default), 'openai', or 'claude'."""
    return os.getenv("PROVIDER", "mock").strip().lower()


def required_keys() -> list[str]:
    return _KEYS.get(provider_name(), [])


def active_model() -> str:
    """The model the active stack will use. Handy for pricing a call *before* you
    make it (the budget check in `app.py` needs this)."""
    return {"mock": _MOCK_MODEL, "openai": _OPENAI_CHAT, "claude": _CLAUDE_CHAT}.get(
        provider_name(), _MOCK_MODEL
    )


def describe() -> str:
    p = provider_name()
    if p == "mock":
        return f"mock  (offline, deterministic, model={_MOCK_MODEL}, no key)"
    if p == "openai":
        return f"openai  (chat={_OPENAI_CHAT})"
    if p == "claude":
        return f"claude  (chat={_CLAUDE_CHAT})"
    return f"unknown provider {p!r}"


def ensure_ready() -> None:
    """Fail fast with a friendly message if the stack isn't configured.

    For PROVIDER=mock this never fails — that's the point. For the real stacks it
    behaves exactly like the guard in the sibling repos.
    """
    import sys

    p = provider_name()
    if p not in _KEYS:
        sys.exit(
            f"PROVIDER={p!r} is not recognized. Set PROVIDER=mock (default), "
            f"openai, or claude in .env."
        )
    missing = [k for k in required_keys() if not os.getenv(k)]
    if missing:
        sys.exit(
            f"PROVIDER={p} needs {', '.join(missing)} in the environment. "
            f"Provide them via secrun (see SECRETS.md), or run `secrun python check_setup.py`. "
            f"(Tip: PROVIDER=mock needs no key and runs everything offline.)"
        )


# ---------------------------------------------------------------------------
# The mock provider — a deterministic, offline "model"
# ---------------------------------------------------------------------------
#
# It answers from a tiny support knowledge base for a fictional product, "Acme
# Cloud." Matching is dumb on purpose (keyword overlap), but deterministic: the
# same question always yields the same answer, which is exactly what lets us
# demonstrate caching (repeat calls hit), evals (a stable gold answer to grade
# against), and cost (token counts you can predict).

_MOCK_KB = {
    "reset password": (
        "To reset your password, open Settings -> Security -> Reset password, "
        "then follow the emailed link. The link expires in 30 minutes."
    ),
    "two factor 2fa authentication": (
        "Turn on two-factor auth under Settings -> Security -> Two-factor. If you "
        "lose your device, use one of the backup codes saved when you enabled it, "
        "or contact support to verify your identity."
    ),
    "refund money back billing": (
        "Refunds are available within 30 days of purchase. Go to Billing -> "
        "History, find the charge, and choose Request refund. It posts in 5-10 days."
    ),
    "cancel subscription plan": (
        "You can cancel anytime under Billing -> Plan -> Cancel. Your plan stays "
        "active until the end of the current billing period; no further charges."
    ),
    "export data download": (
        "Export your data under Settings -> Data -> Export. We build a downloadable "
        "archive and email you a link when it's ready, usually within an hour."
    ),
    "pricing cost plans tiers": (
        "Acme Cloud has three plans: Free (1 project), Pro ($12/mo, unlimited "
        "projects), and Team ($29/user/mo, with shared workspaces and SSO)."
    ),
}

_MOCK_FALLBACK = (
    "I don't have information about that in the Acme Cloud help center. "
    "Please contact support@acme.example for help."
)


# --- Mock-only behavior knobs ----------------------------------------------
# These let examples make the mock *misbehave on purpose* so the reliability and
# budget layers have something real to handle. Real providers ignore all of this.
class _MockBehavior:
    fail_next: int = 0  # raise a transient error on the next N calls, then recover
    latency_ms: float = 5.0  # simulated round-trip time per call


mock = _MockBehavior()


def set_mock_behavior(*, fail_next: int | None = None, latency_ms: float | None = None) -> None:
    """Configure the mock provider for a demo (no effect on real providers)."""
    if fail_next is not None:
        mock.fail_next = fail_next
    if latency_ms is not None:
        mock.latency_ms = latency_ms


def reset_mock_behavior() -> None:
    mock.fail_next = 0
    mock.latency_ms = 5.0


class TransientProviderError(RuntimeError):
    """A retryable error — the kind real SDKs raise on a 429/503/timeout."""


def _approx_tokens(text: str) -> int:
    """A rough token count (~4 chars/token), good enough for cost demos offline."""
    return max(1, len(text) // 4)


def _mock_generate(system: str, user: str) -> LLMResponse:
    # Simulate latency and (optionally) a transient failure.
    time.sleep(mock.latency_ms / 1000.0)
    if mock.fail_next > 0:
        mock.fail_next -= 1
        raise TransientProviderError("mock: simulated transient upstream error (503)")

    q = user.lower()
    best_key, best_score = None, 0
    for key, _answer in _MOCK_KB.items():
        score = sum(1 for word in key.split() if word in q)
        if score > best_score:
            best_key, best_score = key, score
    answer = _MOCK_KB[best_key] if best_key and best_score > 0 else _MOCK_FALLBACK

    # Prompt version can ask for citations; honor it deterministically so prompt
    # versioning (Section 6) produces a visibly different output offline.
    normalized_system = " ".join(system.lower().split())
    if "cite the source" in normalized_system and answer is not _MOCK_FALLBACK:
        answer = f"{answer} (Source: Acme Cloud help center)"

    return LLMResponse(
        text=answer,
        model=_MOCK_MODEL,
        prompt_tokens=_approx_tokens(system + user),
        completion_tokens=_approx_tokens(answer),
        latency_ms=mock.latency_ms,
    )


# --- Real providers: created lazily, so importing this module never forces an
#     SDK import or a network call. ---


@lru_cache(maxsize=1)
def _openai_client():
    from openai import OpenAI

    return OpenAI()


@lru_cache(maxsize=1)
def _anthropic_client():
    import anthropic

    return anthropic.Anthropic()


def generate(system: str, user: str, max_tokens: int = 512) -> LLMResponse:
    """Turn a (system, user) prompt into an `LLMResponse`.

    This is the single seam every ops layer wraps. Note it can *raise* — real
    APIs fail with rate limits and timeouts, and the reliability layer (Section 2)
    exists precisely to handle that. The mock can be told to fail on purpose via
    `set_mock_behavior(fail_next=...)`.
    """
    p = provider_name()
    if p == "mock":
        return _mock_generate(system, user)

    start = time.perf_counter()
    if p == "openai":
        resp = _openai_client().chat.completions.create(
            model=_OPENAI_CHAT,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        latency_ms = (time.perf_counter() - start) * 1000
        usage = resp.usage
        return LLMResponse(
            text=resp.choices[0].message.content or "",
            model=_OPENAI_CHAT,
            prompt_tokens=usage.prompt_tokens if usage else _approx_tokens(system + user),
            completion_tokens=usage.completion_tokens if usage else 0,
            latency_ms=latency_ms,
        )
    if p == "claude":
        resp = _anthropic_client().messages.create(
            model=_CLAUDE_CHAT,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        latency_ms = (time.perf_counter() - start) * 1000
        text = "".join(b.text for b in resp.content if b.type == "text")
        return LLMResponse(
            text=text,
            model=_CLAUDE_CHAT,
            prompt_tokens=resp.usage.input_tokens,
            completion_tokens=resp.usage.output_tokens,
            latency_ms=latency_ms,
        )
    raise ValueError(f"Unknown PROVIDER={p!r} (expected 'mock', 'openai', or 'claude').")
