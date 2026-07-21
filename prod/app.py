"""
prod/app.py: the one app, with every layer wired in.

This is the integration capstone of the whole series: a single function,
`answer(question) -> Answer`, that takes one support question and runs it through
the full production stack, in order:

    observability ─ a trace wraps the whole request
      └─ guardrails (input) ── reject injection / pasted secrets
         └─ prompt registry ── pick the active, versioned system prompt
            └─ cache ──────────── return instantly on a repeat question
               └─ budget ───────── refuse if this call would bust the ceiling
                  └─ reliability ── retry transient failures; fall back if needed
                     └─ MODEL CALL  (mock by default: offline, no key)
               └─ cost ─────────── record what the call actually cost
         └─ guardrails (output) ── redact PII / catch a leaked prompt

Every step writes into the request's trace, so one request produces one
structured, searchable record of exactly what happened and what it cost. Nothing
here is provider-specific. Swap PROVIDER=mock for openai/claude and the same
pipeline runs against a real model.

Each layer lives in its own small module (`prod/observability.py`, `cost.py`,
`reliability.py`, `cache.py`, `guardrails.py`, `prompts.py`) and is taught on its
own in the examples. This file is where they meet.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from prod import cost, guardrails, observability as obs, prompts, providers, reliability
from prod.cache import ResponseCache, cache_key

SAFE_FALLBACK = (
    "Sorry, I can't help with that right now. Please email support@acme.example."
)


@dataclass
class Answer:
    text: str
    trace_id: str
    cached: bool = False
    blocked: bool = False
    cost_usd: float = 0.0
    prompt_version: str = ""
    trace_summary: dict = field(default_factory=dict)


@dataclass
class App:
    """Holds the long-lived pieces: the cache, the spend ceiling, and a circuit
    breaker around the provider. One instance per process (a server would create
    it at startup), so the cache and budget persist across requests."""

    budget: cost.Budget = field(default_factory=lambda: cost.Budget(limit_usd=0.05))
    cache: ResponseCache = field(default_factory=ResponseCache)
    breaker: reliability.CircuitBreaker = field(default_factory=reliability.CircuitBreaker)

    def answer(self, question: str) -> Answer:
        with obs.start_trace("support.answer") as trace:
            version, system = prompts.active_prompt()
            trace.set(prompt_version=version, provider=providers.provider_name())

            # 1. Input guardrail -------------------------------------------------
            with trace.span("guard.input"):
                gin = guardrails.check_input(question)
            if not gin.allowed:
                trace.set(blocked=True, block_reason=gin.reason)
                obs.log("warning", "guard.input.blocked", trace_id=trace.trace_id, reason=gin.reason)
                return Answer(
                    text=SAFE_FALLBACK, trace_id=trace.trace_id, blocked=True,
                    prompt_version=version, trace_summary=trace.summary(),
                )

            # 2. Cache lookup ----------------------------------------------------
            model = providers.active_model()
            key = cache_key(system, question, prompt_version=version, model=model)
            with trace.span("cache.get"):
                hit = self.cache.get(key)
            if hit is not None:
                trace.set(cache="hit", model=hit.model, total_tokens=hit.total_tokens, cost_usd=0.0)
                return self._finish(trace, system, hit.text, version, cached=True, cost_usd=0.0)

            # 3. Budget check (before spending) ---------------------------------
            estimate = cost.price_of(model, _approx_prompt_tokens(system, question), 200)
            with trace.span("budget.check"):
                self.budget.check(estimate)

            # 4. Reliability + the model call -----------------------------------
            def call() -> providers.LLMResponse:
                return self.breaker.call(
                    lambda: reliability.with_retry(
                        lambda: providers.generate(system, question),
                        on_retry=lambda n, e, d: obs.log(
                            "warning", "provider.retry", trace_id=trace.trace_id,
                            attempt=n, error=str(e), backoff_s=round(d, 2),
                        ),
                    )
                )

            with trace.span("model.call"):
                resp = reliability.with_fallback(call, lambda: _fallback_response(model))

            # 5. Cost accounting -------------------------------------------------
            usd = cost.price_of(resp.model, resp.prompt_tokens, resp.completion_tokens)
            self.budget.record(resp.model, usd)
            self.cache.set(key, resp)
            trace.set(
                cache="miss", model=resp.model, total_tokens=resp.total_tokens,
                cost_usd=round(usd, 6), provider_latency_ms=round(resp.latency_ms, 1),
                budget_remaining_usd=round(self.budget.remaining_usd, 4),
            )
            return self._finish(trace, system, resp.text, version, cached=False, cost_usd=usd)

    def _finish(self, trace, system, text, version, *, cached, cost_usd) -> Answer:
        # 6. Output guardrail ---------------------------------------------------
        with trace.span("guard.output"):
            gout = guardrails.check_output(text, system_prompt=system)
        if not gout.allowed:
            trace.set(output_blocked=True, output_block_reason=gout.reason)
            obs.log("warning", "guard.output.blocked", trace_id=trace.trace_id, reason=gout.reason)
            text = SAFE_FALLBACK
        elif gout.redacted is not None:
            trace.set(output_redacted=True)
            text = gout.redacted
        return Answer(
            text=text, trace_id=trace.trace_id, cached=cached, cost_usd=cost_usd,
            prompt_version=version, trace_summary=trace.summary(),
        )


def _approx_prompt_tokens(system: str, user: str) -> int:
    return max(1, (len(system) + len(user)) // 4)


def _fallback_response(model: str) -> providers.LLMResponse:
    """Last resort when even retries fail: a safe canned answer, priced at zero."""
    return providers.LLMResponse(
        text=SAFE_FALLBACK, model=f"{model}/fallback",
        prompt_tokens=0, completion_tokens=0, latency_ms=0.0,
    )


# A process-wide default app, so scripts can just call `prod.app.answer(...)`.
_default = App()


def answer(question: str) -> Answer:
    return _default.answer(question)
