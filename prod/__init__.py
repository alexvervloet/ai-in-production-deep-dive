"""
prod/ : a from-scratch production stack for one LLM app.

Each module is one production concern, taught on its own in `examples/` and wired
together in `app.py`:

    providers.py      the only provider-specific file (mock by default, offline)
    observability.py  traces, spans, structured logs
    cost.py           token -> dollars, plus a budget you can't blow through
    reliability.py    retries, backoff, fallback, circuit breaker
    cache.py          don't pay twice for the same answer
    guardrails.py     input/output checks on the request path
    prompts.py        versioned prompts loaded from prompts/*.txt
    evals.py          the gate: a change ships only if it clears the gold set
    app.py            the one app: answer(question) through every layer

Read them in that order, or jump to app.py to see them meet.
"""
