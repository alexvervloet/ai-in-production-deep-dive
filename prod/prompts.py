"""
prod/prompts.py: treat the prompt as versioned code, not a magic string.

In every teaching repo the system prompt was a string literal next to the call.
That's fine until the day someone "improves" it and quietly breaks a behavior
nobody re-tested. In production the prompt is one of your most important
artifacts and it deserves the same discipline as code: versioned, diffable, and
gated by evals before it ships.

So prompts live in `prompts/*.txt`, one file per version. This registry loads
them and exposes:

  - the **active** version (pinned by `PROMPT_VERSION` in `.env`), so a rollout
    is a config change and a rollback is a one-line revert;
  - **any** version by name, so the eval gate (Section 7) can score v2 against v1
    on the same dataset *before* you flip the default.

Pairs directly with the cache (the version is part of the cache key) and the eval
gate (you don't promote a new version until it passes).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
DEFAULT_VERSION = "v2"


def active_version() -> str:
    """Which prompt version this process serves. Pinned by PROMPT_VERSION in .env."""
    return os.getenv("PROMPT_VERSION", DEFAULT_VERSION).strip()


def available_versions() -> list[str]:
    return sorted(p.stem for p in _PROMPTS_DIR.glob("*.txt"))


@lru_cache(maxsize=None)
def get_prompt(version: str) -> str:
    """Load a specific prompt version's system prompt text."""
    path = _PROMPTS_DIR / f"{version}.txt"
    if not path.exists():
        raise FileNotFoundError(
            f"prompt version {version!r} not found in {_PROMPTS_DIR}. "
            f"Available: {', '.join(available_versions()) or '(none)'}."
        )
    return path.read_text(encoding="utf-8").strip()


def active_prompt() -> tuple[str, str]:
    """Return (version, system_prompt) for the active version."""
    version = active_version()
    return version, get_prompt(version)
