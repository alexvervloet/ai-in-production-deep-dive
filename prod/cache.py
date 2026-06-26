"""
prod/cache.py — don't pay twice for the same answer.
====================================================

Model calls are the slow, expensive part of the app. If two users ask the same
question — or one user retries — there's no reason to call the model again. A
cache turns the second call into a microsecond dictionary lookup, cutting both
latency and cost.

The subtlety is the **key**. An answer is only reusable if *everything that
shaped it* is the same: the user's question, the system prompt, AND the prompt
version. Cache on the question alone and a prompt change silently keeps serving
stale answers. So we hash all of it together — the same discipline the RAG repo
used for its index cache (the embedding model was part of the key).

This is an exact-match cache with TTL. Production often adds a *semantic* cache
(embed the query, reuse the answer for near-duplicates) — a natural extension
once you've done the embeddings repo.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field

from prod.providers import LLMResponse


def cache_key(system: str, user: str, *, prompt_version: str, model: str) -> str:
    """A stable key over everything that determines the answer.

    Change any input — the question, the system prompt, the prompt version, or
    the model — and you get a different key, so you never serve a stale answer
    after a change.
    """
    payload = f"{model}\x1f{prompt_version}\x1f{system}\x1f{user}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class _Entry:
    response: LLMResponse
    stored_at: float


@dataclass
class ResponseCache:
    """An in-process TTL cache. Real deployments back this with Redis so the
    cache is shared across servers and survives a restart — but the interface is
    the same: get / set on a key."""

    ttl_s: float = 3600.0
    hits: int = 0
    misses: int = 0
    _store: dict = field(default_factory=dict)

    def get(self, key: str) -> LLMResponse | None:
        entry = self._store.get(key)
        if entry is None:
            self.misses += 1
            return None
        if time.time() - entry.stored_at > self.ttl_s:
            # Expired: drop it and count a miss, so stale answers age out.
            del self._store[key]
            self.misses += 1
            return None
        self.hits += 1
        # Mark the served copy so logs/observability can tell a cache hit apart.
        cached = LLMResponse(**{**entry.response.__dict__})
        return cached

    def set(self, key: str, response: LLMResponse) -> None:
        self._store[key] = _Entry(response=response, stored_at=time.time())

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0
