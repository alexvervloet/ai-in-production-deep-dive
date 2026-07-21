# Exercises: make the learning stick

Reading code teaches you less than *predicting* what it will do and then checking.
This file turns each section of the [README](README.md) into a few quick
active-recall prompts: a thing to predict, a thing to change, and a question to
answer from memory.

How to use it: work the section first, then come back. **Commit to an answer
before you run or reveal.** The prediction is where the learning happens, even
(especially) when you're wrong. Answers are hidden behind ▸ toggles.

> Everything here is **offline**: the default mock provider needs no key and
> costs nothing. Run as much as you like.

---

## Section 2: The app and the mock

**Predict, then run.** Run `examples/00_mock_provider.py` twice. Will the token
counts differ between runs? Why does that matter for the rest of the repo?

<details><summary>▸ Answer</summary>

No: the mock is deterministic, so the same question yields the same answer and
the same token counts every time. That determinism is what makes caching, cost,
and evals *demonstrable*: a cache hit is provably identical, a cost is
reproducible, and a gold answer is stable to grade against.
</details>

---

## Section 3: Observability

**Recall.** A user reports "the assistant was slow an hour ago." You have a
`print()` in your code. Why doesn't that help, and what does a trace give you that
it doesn't?

<details><summary>▸ Answer</summary>

The `print` is gone. It scrolled past on a screen you weren't watching, for a
request that already finished. A trace is a stored record with a unique id and
per-span timings, so you can find *that* request later and see exactly which span
(cache, model call, guardrails) ate the time.
</details>

**Change it.** In `examples/01_observability.py`, the spans sleep for fixed
durations. Predict which span dominates the trace summary, then change the sleeps
and confirm the `spans` dict tracks your change.

---

## Section 4: Cost

**Predict, then run.** `examples/02_cost.py` sets a $0.0002 budget. Before running,
guess how many of the five questions get answered before `BudgetExceeded`. Then
run it.

<details><summary>▸ Answer</summary>

About five. Each mock call costs ~$0.00003, so the ceiling lands mid-list. The
exact cutoff depends on token counts. The lesson isn't the number: it's that the
budget *refuses the call* rather than spending past the limit. `check()` runs
before the spend; `record()` after.
</details>

---

## Section 5: Reliability

**Predict, then run.** In `examples/03_reliability.py`, the circuit breaker has
`fail_threshold=3`. On which call does it flip from "closed" to "open", and what
changes about calls *after* that?

<details><summary>▸ Answer</summary>

It opens on the 3rd failure. From call 4 on, it fails *fast*, raising "circuit
open" without even calling the provider, until the cooldown elapses. That's the
point: a sick provider stops getting hammered, and your users fail fast instead of
waiting through the full backoff each time.
</details>

**Recall.** Why retry a 503 but not a 400?

<details><summary>▸ Answer</summary>

A 503 is transient: the server is briefly unavailable, and the *same* request
often succeeds on retry. A 400 means the request itself is malformed; retrying it
unchanged just wastes time and quota. Only retry what a retry could fix.
</details>

---

## Section 6: Caching

**Predict, then run.** `examples/04_caching.py` asks the password question under
v1, then again under v2. The text of both answers is nearly identical. Will the
second be a cache HIT? Why or why not?

<details><summary>▸ Answer</summary>

MISS. The cache key hashes the prompt *version* too, not just the question, and
v1 ≠ v2. That's deliberate: a prompt change must invalidate the cache, or you'd
serve answers shaped by the old prompt after shipping a new one.
</details>

---

## Section 7: Guardrails

**Predict, then run.** In `examples/05_guardrails.py`, two outputs contain an
email: one to `jane.doe@gmail.com`, one to `support@acme.example`. Predict what
the output guard does to each.

<details><summary>▸ Answer</summary>

It redacts `jane.doe@gmail.com` (PII) but leaves `support@acme.example` alone 
it's on the allowlist of addresses the app is *supposed* to surface. A blunt PII
filter that scrubs your own help desk email is worse than useless, so every real
redactor needs an allowlist.
</details>

---

## Section 8: Prompt versioning

**Recall.** The prompt lives in `prompts/v2.txt`, not inline in the code. Name two
things that becomes possible because of that.

<details><summary>▸ Answer</summary>

(1) A rollout/rollback is a config change (`PROMPT_VERSION`) or a one-line file
revert, not a code edit. (2) The eval gate can score a new version against the old
one on the same dataset *before* you promote it. Bonus: prompt changes show up in
`git diff` like any other code.
</details>

---

## Section 9: Eval gates

**Predict, then run.** `examples/07_eval_gate.py` scores v1 and v2. One fails the
gate. Which, and on which case? Predict before running.

<details><summary>▸ Answer</summary>

v1 fails the `cite-source` case. Its prompt doesn't ask the model to cite, so the
answer lacks "Source," and the gold set requires it. v2 (constrained, cites
sources) passes all cases. The gate's non-zero exit is what would block the merge
in CI.
</details>

---

## Section 10: The capstone

**Predict, then run.** Start the server
(`python hands_on/serve.py --server --port 8099`) and `curl` the same question
twice, then hit `/metrics`. What will `cache_hit_rate` be, and what will the
second request's `cost_usd` be?

<details><summary>▸ Answer</summary>

The second request is a cache HIT, so its `cost_usd` is 0 and `cache_hit_rate`
climbs toward 0.5 (one hit out of two asks). Every layer you built (trace, guard,
cache, budget, prompt version) is visible in the JSON response and the `/metrics`
summary. That's one operable service, offline, no key.
</details>

---

## Going further: three more production concerns **(offline)**

**Predict (semantic caching, `09`).** "How do I reset my password?" is cached. A new
query "How can I reset my password if I forgot it?" arrives. Exact-match cache: hit or
miss? Semantic cache: hit or miss? What's the danger of setting the threshold too low?

<details><summary>▸ Answer</summary>

Exact-match **misses** (different bytes); the semantic cache **hits** (high similarity)
and saves a call. Too low a threshold and you serve a **similar-but-wrong** cached
answer to a genuinely different question, so you tune it, and keep exact-match for
things that must never be confused.
</details>

**Recall (fallback/routing, `10`).** Name the two ways a *second* model helps, and how
each differs from the retries in Section 5.

<details><summary>▸ Answer</summary>

**Failover**: when the primary is down even after retries, serve from a backup
(cheaper model or canned answer) instead of erroring. **Cost routing**: send easy
questions to a cheap model, hard ones to the expensive model, to cut the bill. Retries
re-call the *same* model; these reach for a *different* one.
</details>

**Recall (rate limiting & feedback, `11`).** What does a per-tenant token bucket
protect against, and why is a thumbs-down the most valuable signal you can log?

<details><summary>▸ Answer</summary>

It stops any one client/tenant from **starving a shared, costly backend** (fairness,
cost control, multi-tenancy), so one tenant's burst is capped without affecting others.
A 👎 is a **labelled example of something your system got wrong**, exactly the
regression test (evals dive) and fine-tuning data that makes the next version better.
</details>

---

**Done?** You've operated one app end to end. The "Where to go next" section of
the README maps each from-scratch layer here to its industrial counterpart, same
interfaces, bigger machinery.
