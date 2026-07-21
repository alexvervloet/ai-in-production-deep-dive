"""
prod/guardrails.py: check what comes in and what goes out.

The prompt-injection deep dive (repo #6) built these defenses in isolation, one
demo per technique. Production's job is to put them *on the request path* so
every call is checked, in the right order, with the result recorded in the trace.

Two gates:

  1. **Input guard** (before the model): reject obvious prompt-injection
     attempts and refuse to forward secrets/PII the user shouldn't be pasting.
     This is the "necessary, not sufficient" layer from the injection repo:
     cheap, catches the easy stuff, never your only defense.
  2. **Output guard** (after the model): catch a leaked system prompt, an empty
     answer, or PII in the response before it reaches the user.

Each gate returns a `GuardResult` (allowed + reason) rather than raising, so the
app can decide what to do (block, redact, or fall back to a safe message) and
log the decision either way. The patterns here are intentionally simple; the
point is *where* they sit, not regex cleverness.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Crude but illustrative. Real systems layer an ML/LLM classifier on top of rules
# like these (see Section 6 of the prompt-injection repo).
_INJECTION_PATTERNS = [
    r"ignore (all |the |your )?(previous|prior|above) instructions",
    r"disregard (the |your )?(system|previous) (prompt|instructions)",
    r"reveal (your|the) (system )?prompt",
    r"you are now",
    r"developer mode",
]

# A couple of common PII shapes. Not exhaustive: a real DLP layer does far more.
_PII_PATTERNS = {
    "credit_card": r"\b(?:\d[ -]?){13,16}\b",
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "email": r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b",
}

# Addresses the app is *supposed* to surface (its own support contact) must not be
# redacted. A blunt PII filter that scrubs your own help desk email is worse than
# useless: so every real redactor needs an allowlist of known-safe values.
_ALLOWLIST = {"support@acme.example"}


@dataclass
class GuardResult:
    allowed: bool
    reason: str = ""
    redacted: str | None = None  # set when the guard cleaned the text instead of blocking

    @classmethod
    def ok(cls) -> "GuardResult":
        return cls(allowed=True)


def check_input(text: str) -> GuardResult:
    """Run before the model. Block injection attempts; flag pasted secrets."""
    lowered = text.lower()
    for pattern in _INJECTION_PATTERNS:
        if re.search(pattern, lowered):
            return GuardResult(allowed=False, reason="possible prompt injection in input")
    for label, pattern in (("credit_card", _PII_PATTERNS["credit_card"]), ("ssn", _PII_PATTERNS["ssn"])):
        if re.search(pattern, text):
            return GuardResult(allowed=False, reason=f"input contains what looks like a {label}")
    return GuardResult.ok()


def check_output(text: str, *, system_prompt: str = "") -> GuardResult:
    """Run after the model. Catch leaks, empties, and PII before the user sees it."""
    if not text.strip():
        return GuardResult(allowed=False, reason="empty model output")

    # Did the model parrot back a distinctive chunk of the system prompt?
    if system_prompt:
        marker = system_prompt.strip().split("\n", 1)[0][:40]
        if marker and marker.lower() in text.lower():
            return GuardResult(allowed=False, reason="output appears to leak the system prompt")

    # Redact PII rather than blocking: a useful answer with a masked email beats
    # no answer.
    redacted = text
    for label, pattern in _PII_PATTERNS.items():
        def _mask(m: "re.Match") -> str:
            return m.group(0) if m.group(0) in _ALLOWLIST else f"[redacted-{label}]"

        redacted = re.sub(pattern, _mask, redacted)
    if redacted != text:
        return GuardResult(allowed=True, reason="redacted PII in output", redacted=redacted)

    return GuardResult.ok()
