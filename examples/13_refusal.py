#!/usr/bin/env python3
"""
13_refusal.py: the failure that returns HTTP 200.

    python examples/13_refusal.py            # offline, no key

Every other failure in this repo announces itself. A rate limit raises. A timeout
raises. A 500 raises. Section 5's retry layer and Section 10's fallback layer are
both built on that: something threw, so we caught it and did something else.

A refusal doesn't throw. A safety classifier declines the request, the API returns
**200 OK**, and the response body says so in `stop_reason` instead of in an
exception. The text field is empty. Nothing in your `try/except` fires, nothing in
your retry policy triggers, and your error rate stays flat.

So the bug is quiet by construction. Code that reads `response.text` and moves on
ships an empty string to a user, writes an empty string to a cache, and scores an
empty string in an eval, all while every dashboard stays green. That is worse than
an outage, because an outage pages someone.

Three things this shows:

  1. WHAT NAIVE CODE DOES. The same happy-path shape everyone writes.
  2. WHAT IT COSTS. The empty answer reaching a user, and the cache poisoning it
     causes on the way.
  3. THE FIX. Check `stop_reason` before you read `text`, and treat a refusal as
     its own outcome rather than as an error or as an answer.

Run it:

    python examples/13_refusal.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from prod import providers

load_dotenv()

QUESTION = "How do I reset my password?"
SYSTEM = "You are the Acme Cloud support assistant."


def rule() -> None:
    print("-" * 64)


# --- 1. The naive read: no exception, no answer, no signal -------------------
def demo_naive():
    print("1) NAIVE: read .text and move on\n" + "-" * 64)
    providers.set_mock_behavior(refuse_next=1)

    try:
        resp = providers.generate(SYSTEM, QUESTION)
        # This is the line almost everyone writes.
        answer = resp.text
        print("   no exception raised:      True")
        print(f"   answer shown to the user: {answer!r}")
        print(f"   characters delivered:     {len(answer)}")
    except Exception as exc:  # noqa: BLE001 - the point is that this never runs
        print(f"   caught: {exc}")

    print("\n   Nothing was raised, so nothing retried and nothing alerted.")
    print("   The user got an empty reply and the error rate stayed at zero.")


# --- 2. The second-order cost: an empty answer is a cacheable answer ---------
def demo_cache_poisoning():
    print("\n2) THE COST: a refusal cached is a refusal served forever\n" + "-" * 64)
    cache: dict[str, str] = {}

    providers.set_mock_behavior(refuse_next=1)
    for attempt in (1, 2, 3):
        if QUESTION in cache:
            print(f"   attempt {attempt}: cache HIT  -> {cache[QUESTION]!r}")
            continue
        resp = providers.generate(SYSTEM, QUESTION)
        cache[QUESTION] = resp.text  # storing without checking why it stopped
        print(f"   attempt {attempt}: cache MISS -> stored {resp.text!r}")

    print("\n   The refusal happened once. The cache now serves it to everyone,")
    print("   including the requests the model would happily have answered.")
    print("   Section 6's cache and Section 13's refusal have to know about each other.")


# --- 3. The fix: a refusal is its own outcome -------------------------------
def answer_question(system: str, user: str) -> tuple[str, str]:
    """Return (outcome, text). Outcome is one of: answered, refused, error."""
    try:
        resp = providers.generate(system, user)
    except providers.TransientProviderError as exc:
        return "error", str(exc)

    # The guard. Before anything reads .text, ask why generation stopped.
    if resp.refused:
        return "refused", resp.stop_category or "unspecified"
    if not resp.text.strip():
        # Belt and braces: an empty body with a normal stop_reason is still not
        # an answer, and it should never be cached or scored as one.
        return "error", "empty response with stop_reason=end_turn"
    return "answered", resp.text


def demo_fix():
    print("\n3) THE FIX: branch on stop_reason before reading text\n" + "-" * 64)
    cache: dict[str, str] = {}

    providers.set_mock_behavior(refuse_next=1)
    for attempt in (1, 2):
        outcome, payload = answer_question(SYSTEM, QUESTION)
        if outcome == "answered":
            cache[QUESTION] = payload  # only a real answer is cacheable
            print(f"   attempt {attempt}: answered -> {payload[:46]}...")
        elif outcome == "refused":
            print(f"   attempt {attempt}: REFUSED  -> category={payload}, nothing cached")
        else:
            print(f"   attempt {attempt}: error    -> {payload}")

    print(f"\n   cache contents: {len(cache)} entry (the answer, not the refusal)")


def main():
    print("=" * 64)
    print("REFUSALS: the failure mode that returns 200 OK")
    print("=" * 64 + "\n")

    demo_naive()
    demo_cache_poisoning()
    demo_fix()

    providers.reset_mock_behavior()

    print("\n" + "=" * 64)
    print("""
What to take away:

  A refusal is a THIRD outcome, not an error and not an answer. Code that
  models only two will mislabel it, and it will mislabel it silently.

  Check the stop reason before you read the text. On Claude that's
  `stop_reason == "refusal"`, with the category in `stop_details` (which is
  None for every other stop reason, so guard before reading it). On OpenAI a
  declined request comes back on `message.refusal` rather than
  `message.content`.

  Count refusals separately. They belong on the dashboard next to errors and
  next to successes, because a refusal rate that moves is either a policy
  change upstream or a prompt of yours that started tripping a classifier, and
  you want to know which. Section 1's observability layer is where that
  counter goes.

  Decide what happens next, deliberately. A refusal can route to a different
  model, to a canned response, or to a human. What it must not do is fall
  through as an empty string. Anthropic's server-side fallbacks will do the
  routing for you if you opt in; either way the decision is yours to make
  before it happens, not after a user reports a blank reply.
""")
    print("=" * 64)


if __name__ == "__main__":
    main()
