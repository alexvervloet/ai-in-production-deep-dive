#!/usr/bin/env python3
"""
04_caching.py: don't pay twice for the same answer.

    python examples/04_caching.py            # offline, no key

A repeat question should be a dictionary lookup, not another model call. We ask
the same question twice (cache hit, instant, free), a different question (miss),
and then show the subtle part: changing the prompt version changes the key, so a
prompt update correctly *invalidates* the cache instead of serving a stale
answer.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from prod import providers
from prod.cache import ResponseCache, cache_key

load_dotenv()

cache = ResponseCache()
SYSTEM_V1 = "You are the Acme Cloud support assistant."
SYSTEM_V2 = "You are the Acme Cloud support assistant. Always cite the source."


def ask(question: str, system: str, version: str) -> None:
    model = providers.active_model()
    key = cache_key(system, question, prompt_version=version, model=model)
    hit = cache.get(key)
    if hit is not None:
        print(f"HIT   [{version}] {question!r}  (0 tokens, $0)")
        return
    resp = providers.generate(system, question)
    cache.set(key, resp)
    print(f"MISS  [{version}] {question!r}  ({resp.total_tokens} tokens -> called model)")


ask("How do I reset my password?", SYSTEM_V1, "v1")
ask("How do I reset my password?", SYSTEM_V1, "v1")   # same -> HIT
ask("Can I get a refund?", SYSTEM_V1, "v1")           # different -> MISS
ask("How do I reset my password?", SYSTEM_V2, "v2")   # prompt changed -> MISS (correct!)

print(f"\nCache: {cache.hits} hits / {cache.misses} misses  (hit rate {cache.hit_rate:.0%})")
print("\nThe key hashes question + system + prompt version + model. Anything that")
print("changes the answer changes the key, so you never serve a stale answer.")
