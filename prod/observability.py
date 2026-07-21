"""
prod/observability.py: see what your app actually did.

In the teaching repos, "observability" was a `print()`. That's fine when you're
the only user and the failure is on your screen. In production the request is
gone in 80ms, it happened to someone else, and "it was slow" or "it gave a weird
answer" is all you get. You need a record you can search.

Three ideas, built from scratch with the standard library:

  1. A **trace**: one object per request, carrying a unique id so every log line
     and timing for that request can be stitched back together later.
  2. **Spans**: timed sections within a request (guardrails, cache lookup, the
     model call), so you can see *where the time went*.
  3. **Structured logs**: one JSON object per event instead of prose, so you can
     filter by trace_id, latency, or error in any log tool.

This is a teaching-sized version of what OpenTelemetry + a backend (Honeycomb,
Datadog, Grafana) give you. The shapes are deliberately the same: trace id,
spans, attributes.
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass
class Span:
    name: str
    start_ms: float
    duration_ms: float = 0.0
    attributes: dict = field(default_factory=dict)


@dataclass
class Trace:
    """Everything we recorded about one request."""

    trace_id: str
    name: str
    start_ms: float
    spans: list[Span] = field(default_factory=list)
    attributes: dict = field(default_factory=dict)
    duration_ms: float = 0.0

    def set(self, **attrs) -> None:
        """Attach top-level facts about the request (model, tokens, cost, ...)."""
        self.attributes.update(attrs)

    @contextmanager
    def span(self, name: str, **attributes):
        """Time a section of work. Use as `with trace.span("cache"): ...`."""
        span = Span(name=name, start_ms=(time.perf_counter() * 1000), attributes=dict(attributes))
        start = time.perf_counter()
        try:
            yield span
        finally:
            span.duration_ms = (time.perf_counter() - start) * 1000
            self.spans.append(span)

    def summary(self) -> dict:
        """A flat dict for logging / dashboards."""
        return {
            "trace_id": self.trace_id,
            "request": self.name,
            "duration_ms": round(self.duration_ms, 1),
            **self.attributes,
            "spans": {s.name: round(s.duration_ms, 1) for s in self.spans},
        }


# --- The logger ------------------------------------------------------------
# One JSON object per line ("structured logging"). A real deployment ships these
# to a log aggregator; here we just write to stderr so stdout stays clean for the
# app's actual answer.

_LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40}
_min_level = "info"


def set_level(level: str) -> None:
    global _min_level
    _min_level = level


def log(level: str, event: str, **fields) -> None:
    """Emit one structured log line. `event` is a short machine-friendly name."""
    if _LEVELS.get(level, 20) < _LEVELS.get(_min_level, 20):
        return
    record = {"ts": round(time.time(), 3), "level": level, "event": event, **fields}
    print(json.dumps(record, default=str), file=sys.stderr)


@contextmanager
def start_trace(name: str):
    """Open a trace for one request. Yields a `Trace`; logs a summary on exit."""
    trace = Trace(trace_id=uuid.uuid4().hex[:12], name=name, start_ms=time.perf_counter() * 1000)
    start = time.perf_counter()
    log("info", "request.start", trace_id=trace.trace_id, request=name)
    try:
        yield trace
    except Exception as exc:
        trace.duration_ms = (time.perf_counter() - start) * 1000
        trace.set(error=type(exc).__name__, error_message=str(exc))
        log("error", "request.error", **trace.summary())
        raise
    else:
        trace.duration_ms = (time.perf_counter() - start) * 1000
        log("info", "request.end", **trace.summary())
