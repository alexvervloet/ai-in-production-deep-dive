#!/usr/bin/env python3
"""
09_semantic_caching.py: cache by MEANING, not exact text.

    python examples/09_semantic_caching.py            # offline, no key

The exact-match cache (Section 6) only hits when the question is byte-for-byte
identical. But "how do I reset my password?" and "I forgot my password, help" want
the *same* answer, and an exact cache misses every paraphrase. A **semantic cache**
fixes that: embed the query, and serve a cached answer when a previous query is
close enough in meaning (cosine similarity above a threshold).

The payoff is a much higher hit rate on real traffic (users phrase things a hundred
ways). The risk is the dial: too low a threshold and you serve the *wrong* cached
answer to a merely-similar question (a false hit). So you tune the threshold and
keep exact-match for the things that must never be confused.

This runs fully offline: we use a tiny bag-of-words cosine in place of a real
embedding model, so you can see the mechanism without a key. (Swap in
`providers`-style embeddings for production.)

Run it:

    python examples/09_semantic_caching.py
"""

import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from prod import providers

load_dotenv()


def embed(text: str) -> dict[str, float]:
    """A stand-in 'embedding': a bag-of-words vector. Real systems use a model;
    the caching logic is identical: a vector and a cosine."""
    words = re.findall(r"[a-z']+", text.lower())
    vec: dict[str, float] = {}
    for w in words:
        vec[w] = vec.get(w, 0.0) + 1.0
    return vec


def cosine(a: dict, b: dict) -> float:
    keys = set(a) & set(b)
    dot = sum(a[k] * b[k] for k in keys)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


class SemanticCache:
    def __init__(self, threshold: float = 0.6) -> None:
        self.threshold = threshold
        self.entries: list[tuple[dict, str, str]] = []  # (vector, query, answer)
        self.hits = 0
        self.misses = 0

    def get(self, query: str):
        qv = embed(query)
        best_sim, best = 0.0, None
        for vec, q, ans in self.entries:
            sim = cosine(qv, vec)
            if sim > best_sim:
                best_sim, best = sim, (q, ans)
        if best and best_sim >= self.threshold:
            self.hits += 1
            return best[1], best_sim, best[0]
        self.misses += 1
        return None

    def set(self, query: str, answer: str) -> None:
        self.entries.append((embed(query), query, answer))


SYSTEM = "You are the Acme Cloud support assistant."
cache = SemanticCache(threshold=0.6)


def ask(question: str) -> None:
    hit = cache.get(question)
    if hit is not None:
        answer, sim, matched = hit
        print(f"HIT  (sim {sim:.2f} ~ {matched!r})\n     {question!r} -> {answer[:60]}...")
        return
    resp = providers.generate(SYSTEM, question)
    cache.set(question, resp.text)
    print(f"MISS (called model, {resp.total_tokens} tokens)\n     {question!r}")


if __name__ == "__main__":
    print(f"Provider: {providers.describe()}   |  semantic threshold = {cache.threshold}\n")
    ask("How do I reset my password?")                  # MISS -> caches it
    ask("How do I reset my password?")                  # exact repeat -> HIT
    ask("How can I reset my password if I forgot it?")  # PARAPHRASE -> semantic HIT
    ask("Can I get a refund on my subscription?")       # different topic -> MISS

    print(f"\nCache: {cache.hits} hits / {cache.misses} misses "
          f"(hit rate {cache.hits / (cache.hits + cache.misses):.0%})")
    print(
        "\nThe paraphrase HIT is the whole point; an exact cache would have missed it\n"
        "and paid for another call. Tune the threshold carefully: too low and you serve\n"
        "a similar-but-wrong cached answer. Semantic caching trades a little risk for a\n"
        "much higher hit rate; keep exact-match for things that must never be confused."
    )
