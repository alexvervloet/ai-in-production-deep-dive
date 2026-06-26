#!/usr/bin/env python3
"""
01_observability.py — one request, one searchable record.
=========================================================

    python examples/01_observability.py            # offline, no key

A `print()` tells you what happened on *your* screen. A trace tells you what
happened on a request that already finished, for someone else, in production.
Here we open a trace, time three spans inside it, and emit structured (JSON) log
lines. Notice the trace_id threading every line together, and the per-span
timings showing where the work went.

Logs go to stderr; the "answer" goes to stdout — so in a real pipe your
telemetry never pollutes your output.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prod import observability as obs

obs.set_level("debug")

with obs.start_trace("demo.request") as trace:
    with trace.span("retrieve"):
        time.sleep(0.02)  # pretend we looked something up
    with trace.span("model.call", model="mock-1"):
        time.sleep(0.05)  # pretend we called the model
        trace.set(tokens=128)
    with trace.span("postprocess"):
        time.sleep(0.005)
    trace.set(answer_chars=64)

print("\nThe trace summary (one dict you could ship to any dashboard):")
import json

# Re-open to show a summary object; in app.py this is logged automatically.
print(json.dumps(trace.summary(), indent=2, default=str))
print("\nIn production this goes to OpenTelemetry + a backend. The shape is the same:")
print("trace id, spans, attributes — filter by any of them when something breaks.")
