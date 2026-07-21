"""
prod/cost.py: turn tokens into dollars, and stop before you overspend.

The OpenAI and Claude deep dives taught you to *estimate* cost: tokens times a
price. In production that estimate has two new jobs:

  1. **Attribution**: record what every request cost, tagged with its trace id,
     so you can answer "what did this customer / endpoint / day cost?"
  2. **Enforcement**: a budget the app refuses to blow through. A runaway loop
     or an abuse spike should hit a ceiling and stop, not show up as a surprise
     invoice.

Prices below are per *million* tokens, matching how providers publish them. The
mock provider has no real cost, but we price it anyway (at the gpt-4o-mini rate)
so the budget machinery is demonstrable offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# USD per 1M tokens, (input, output). Keep these in one place so a price change is
# a one-line edit. (Illustrative: confirm current prices with your provider.)
_PRICES = {
    "mock-1": (0.15, 0.60),
    "gpt-4o-mini": (0.15, 0.60),
    "claude-haiku-4-5": (1.00, 5.00),
}


def price_of(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Dollar cost of one call. Unknown models price at 0 (and should be added)."""
    in_price, out_price = _PRICES.get(model, (0.0, 0.0))
    return (prompt_tokens * in_price + completion_tokens * out_price) / 1_000_000


class BudgetExceeded(RuntimeError):
    """Raised when a request would push spend past the configured ceiling."""


@dataclass
class Budget:
    """A running spend ceiling. One per process here; per-customer in real life.

    `check()` is called *before* a model call with the call's estimated cost, so
    the app can refuse instead of spending. `record()` is called *after*, with
    the actual cost, to advance the meter.
    """

    limit_usd: float
    spent_usd: float = 0.0
    calls: int = 0
    by_model: dict = field(default_factory=dict)

    def check(self, estimated_usd: float) -> None:
        if self.spent_usd + estimated_usd > self.limit_usd:
            raise BudgetExceeded(
                f"budget ${self.limit_usd:.4f} would be exceeded "
                f"(spent ${self.spent_usd:.4f}, this call ~${estimated_usd:.4f})"
            )

    def record(self, model: str, usd: float) -> None:
        self.spent_usd += usd
        self.calls += 1
        self.by_model[model] = self.by_model.get(model, 0.0) + usd

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.limit_usd - self.spent_usd)
