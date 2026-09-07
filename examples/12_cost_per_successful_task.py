#!/usr/bin/env python3
"""
12_cost_per_successful_task.py: the number that decides whether to keep the workflow.

    python examples/12_cost_per_successful_task.py     # offline, no key, pure arithmetic

§4 answered "what did this call cost?" That is the number on the provider dashboard,
and it is not the number anyone should be making decisions with. A workflow does not
sell calls, it finishes tasks, and between a call and a finished task sit retries,
failures, and the person who has to fix the output before it can be used.

Three numbers, in increasing order of usefulness:

  1. cost per call        - total spend / calls made. What the dashboard shows.
  2. cost per task        - total spend / tasks attempted. Now retries count.
  3. cost per SUCCESSFUL  - (spend + human correction) / tasks that actually landed.
     task                   The only one you can compare against the thing the
                            workflow replaced.

The third one is usually a shock the first time you compute it, and it usually says
something different from what the team assumed. This script computes all three on a
deterministic month of traffic, then asks the question the numbers exist to answer:
is this cheaper than the alternative, and what would actually move it?
"""

import os
import random
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prod import cost

# A retrieval-backed workflow, so the prompt carries real context rather than a
# one-line question. Cheap models with short prompts make the arithmetic below look
# rigged; this is closer to what a working system actually sends.
MODEL = "claude-haiku-4-5"
PROMPT_TOKENS = 20_000     # retrieved passages + instructions
COMPLETION_TOKENS = 800

# What the workflow replaced, and what it costs to run the humans in it. Both are
# assumptions, and both belong at the top of the file where someone can argue with
# them, rather than buried in a spreadsheet nobody opens.
BASELINE_MINUTES_PER_TASK = 6.0   # a person doing the whole task by hand
LOADED_HOURLY_USD = 45.0          # salary + payroll + benefits + overhead, not salary
TASKS = 500


@dataclass
class TaskRun:
    """One task's whole life, including everything it took to finish it."""

    attempts: int            # model calls, so a retry after a failure counts twice
    prompt_tokens: int
    completion_tokens: int
    succeeded: bool          # did it produce something usable at all?
    review_minutes: float    # human time spent checking and fixing it

    @property
    def model_usd(self) -> float:
        return self.attempts * cost.price_of(MODEL, self.prompt_tokens, self.completion_tokens)

    @property
    def human_usd(self) -> float:
        return self.review_minutes / 60.0 * LOADED_HOURLY_USD


def simulate(n: int, seed: int = 7) -> list[TaskRun]:
    """A month of traffic with the failure modes a real workflow has.

    The shape here is the point, not the exact draws. Most tasks work on the first
    call and get a quick skim. Some need a retry. Some never produce anything usable
    and fall back to a human doing the whole thing. And a slice of the *successful*
    ones still need real editing before they can be used, which is the cost that
    hides best, because nothing in the system records it.
    """
    rng = random.Random(seed)
    runs = []
    for _ in range(n):
        roll = rng.random()
        p, c = PROMPT_TOKENS, COMPLETION_TOKENS
        if roll < 0.78:                                     # clean pass
            runs.append(TaskRun(1, p, c, True, rng.uniform(0.3, 0.8)))
        elif roll < 0.90:                                   # retried, then fine
            runs.append(TaskRun(2, p, c, True, rng.uniform(0.5, 1.2)))
        elif roll < 0.97:                                   # usable but needs editing
            runs.append(TaskRun(1, p, c, True, rng.uniform(2.5, 5.0)))
        else:                                               # failed, human redoes it
            runs.append(TaskRun(2, p, c, False, BASELINE_MINUTES_PER_TASK))
    return runs


def report(runs: list[TaskRun]) -> None:
    calls = sum(r.attempts for r in runs)
    wins = [r for r in runs if r.succeeded]
    model_usd = sum(r.model_usd for r in runs)
    human_usd = sum(r.human_usd for r in runs)
    total_usd = model_usd + human_usd

    print(f"{len(runs)} tasks, {calls} model calls, {len(wins)} finished without a redo")
    print(f"  model spend:  ${model_usd:8.2f}")
    print(f"  human effort: ${human_usd:8.2f}   ({sum(r.review_minutes for r in runs):.0f} minutes)")
    print(f"  total:        ${total_usd:8.2f}\n")

    print(f"  1. cost per call             ${model_usd / calls:.4f}   <- the dashboard number")
    print(f"  2. cost per task attempted   ${model_usd / len(runs):.4f}   <- retries now count")
    print(f"  3. cost per SUCCESSFUL task  ${total_usd / len(wins):.4f}   <- with the humans in it")
    print()

    share = model_usd / total_usd
    print(f"  The model is {share:.1%} of what a finished task costs.")
    print(f"  The people are {1 - share:.1%}, and only number 3 can see them.\n")

    baseline_usd = len(runs) * BASELINE_MINUTES_PER_TASK / 60.0 * LOADED_HOURLY_USD
    saved = baseline_usd - total_usd
    print(f"  {f'all {len(runs)} by hand':<22}${baseline_usd:9.2f}")
    print(f"  {'with the workflow':<22}${total_usd:9.2f}")
    print(f"  {'saved':<22}${saved:9.2f}   ({saved / baseline_usd:.0%})")
    print("  That comparison, not the accuracy score, is what justifies the workflow.\n")


def sensitivity(runs: list[TaskRun]) -> None:
    """Which lever actually moves the number, and which one everyone reaches for."""
    model_usd = sum(r.model_usd for r in runs)
    human_usd = sum(r.human_usd for r in runs)
    wins = sum(1 for r in runs if r.succeeded)
    base = (model_usd + human_usd) / wins

    cheaper_model = (model_usd / 10 + human_usd) / wins
    less_review = (model_usd + human_usd / 2) / wins

    print("Two proposals, one quarter of engineering time each:\n")
    print(f"  today                       ${base:.4f} per successful task")
    print(f"  a model 10x cheaper         ${cheaper_model:.4f}  ({cheaper_model / base - 1:+.1%})")
    print(f"  half the human review time  ${less_review:.4f}  ({less_review / base - 1:+.1%})")
    print(
        "\n  A tenfold cut in the model bill barely registers. Halving review time is\n"
        "  the whole ballgame. This is the normal shape once a person is in the loop,\n"
        "  and it is the opposite of where cost conversations usually go, because the\n"
        "  model bill is the part with a dashboard and the review time is the part\n"
        "  nobody instruments.\n"
    )


if __name__ == "__main__":
    print("Cost per successful task\n" + "=" * 64)
    runs = simulate(TASKS)
    report(runs)
    print("What to work on\n" + "=" * 64)
    sensitivity(runs)
    print(
        "Takeaway: price the outcome, not the call. Put the denominator on tasks that\n"
        "actually landed, put human correction time in the numerator at a loaded rate,\n"
        "and compare the result to whatever you are replacing. Do that and the cheapest\n"
        "workflow is rarely the one with the cheapest model. Skip it and you will\n"
        "optimize a bill that was never the problem.\n\n"
        "To measure review time for real rather than assuming it: log an edit-distance\n"
        "between what the model produced and what the human shipped, or just time the\n"
        "review queue. The thumbs-up/down loop in example 11 is where that hooks in."
    )
