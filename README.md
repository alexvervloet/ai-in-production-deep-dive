# Production — A Guided Deep Dive

A hands-on playground for the part the other repos skipped: **everything that
wraps the model call once real users depend on it.** You'll take one small app —
a customer-support assistant — and operate it end to end, building each
production concern from scratch: observability, cost control, reliability,
caching, guardrails, prompt versioning, and eval gates. No framework, no
platform, no SaaS dashboard — just enough code to *see* how each one works.

The twist that makes this repo work: it runs **completely offline on a mock
provider**, with no API key. The whole point here is the machinery *around* the
model, so we ship a tiny deterministic "model" in-process and wrap it in the real
ops stack. Every example, the eval gate, and the capstone server run with zero
keys and zero cost. Flip one env var and the exact same pipeline runs against a
real OpenAI or Claude model.

This is the eighth and final core repo in the series. The first seven teach the pieces —
[the API](https://github.com/Ailuue/openai-api-deep-dive),
[Claude](https://github.com/Ailuue/claude-api-deep-dive),
[prompt engineering](https://github.com/Ailuue/prompt-engineering-deep-dive),
[RAG](https://github.com/Ailuue/rag-deep-dive),
[evals](https://github.com/Ailuue/evals-deep-dive),
[agents](https://github.com/Ailuue/agents-deep-dive), and
[guardrails](https://github.com/Ailuue/prompt-injection-deep-dive). Each of those
ends with a section called **"From teaching code to production."** This repo *is*
that section, made runnable.

Like its siblings, it's meant to be *walked through*, not just read. Each section
ends with something to run. Do the running — that's where the learning is. And
[EXERCISES.md](EXERCISES.md) has a predict-then-run prompt for each section.

---

## 0. The one big idea

A prototype answers the question. A production system answers the question *and*
can tell you what it cost, prove what it did, survive the provider having a bad
day, refuse to overspend, not get jailbroken, and not regress when someone edits
the prompt. All of that is the same shape:

> **The model call is one line. Production is the dozen lines around it that make
> that one line safe, cheap, observable, and reliable — on every request.**

Each section below is one of those concerns, built on its own, then wired into a
single `answer(question)` function in [prod/app.py](prod/app.py). That function is
the whole repo: one request, every layer, in order.

---

## 1. Setup (5 minutes)

```bash
# 1. Create an isolated Python environment
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 2. Install dependencies (just python-dotenv for the default offline stack)
pip install -r requirements.txt

# 3. Copy the env file — the default runs keyless (no API key needed)
cp .env.example .env
#    (Real provider instead of the mock? Its key goes in your OS keychain,
#     not .env — see ../SECRETS.md — then run scripts as `secrun python ...`.)

# 4. Confirm everything is wired up (makes no API call, costs nothing)
python check_setup.py
```

That's it — no key required. The default `PROVIDER=mock` is a real, in-process
model that answers from a built-in support knowledge base. Pick your stack with
`PROVIDER` in `.env`:

| `PROVIDER` | What runs the model | Keys needed | Cost |
|------------|---------------------|-------------|------|
| `mock` (default) | a deterministic offline "model" | **none** | **$0** |
| `openai` | OpenAI `gpt-4o-mini` | `OPENAI_API_KEY` | tiny |
| `claude` | Claude `claude-haiku-4-5` | `ANTHROPIC_API_KEY` | tiny |

The production stack is identical on all three — the only file that knows which
you picked is [prod/providers.py](prod/providers.py). That's the whole point:
observability, cost, retries, caching, guardrails, prompt versioning, and eval
gates are not provider features. They're things *you* build around the call.

> 💡 **Everything in this repo runs offline.** No key, no network, no cost — that
> is what lets us demonstrate cost dashboards, retries, and eval gates with a
> model that fails (and succeeds) exactly when we tell it to.

---

## 2. The app, and the mock that powers it

The thing we're operating is a support assistant for a fictional product, "Acme
Cloud." Ask it a question; it answers from a small knowledge base. That's the
prototype every sibling repo would call done.

The mock provider ([prod/providers.py](prod/providers.py)) makes it operable
offline. It's deterministic — the same question always yields the same answer and
the same token counts — which is exactly what lets us demonstrate caching (the
repeat is a hit), evals (a stable answer to grade), and cost (token counts you can
predict). It also reports latency and can be told to **fail on purpose**, so the
reliability layer has something real to handle.

```bash
python examples/00_mock_provider.py
```

Every layer below calls `providers.generate(system, user)` and gets back an
`LLMResponse` — the answer plus the metadata production code needs: token counts,
the model name, and latency. Real providers fill those from the API; the mock
computes them locally.

---

## 3. Observability — see what it actually did

In the teaching repos, "observability" was a `print()`. That works when the
failure is on your screen. In production the request finished 80ms ago, for
someone else, and "it was slow" is all you get. You need a *record you can
search.*

[prod/observability.py](prod/observability.py) builds three things from the
standard library: a **trace** (one object per request, with a unique id), **spans**
(timed sections — guardrails, cache, model call — so you see where the time went),
and **structured logs** (one JSON object per event, filterable by trace id,
latency, or error). It's a teaching-sized OpenTelemetry: same shapes, no backend.

```bash
python examples/01_observability.py
```

> ⚠️ **Your logs are a PII sink.** A trace records request fields, so naive
> logging is the fastest way to leak the exact data your output guard (Section 7)
> just redacted — now sitting in plaintext in a log store that usually has looser
> access controls and longer retention than your database. Same discipline as the
> output guard: scrub structured fields before they leave the process, and never
> log a raw prompt or answer verbatim. Observability and PII are the same problem
> seen from two sides.

---

## 4. Cost — turn tokens into dollars, and refuse to overspend

The API repos taught you to *estimate* cost. Production gives that estimate two
jobs: **attribution** (record what every request cost, tagged with its trace id)
and **enforcement** (a budget the app won't blow through). A runaway loop or an
abuse spike should hit a ceiling and stop — not show up as a surprise invoice.

[prod/cost.py](prod/cost.py) prices a call from its token counts and exposes a
`Budget` you `check()` before spending and `record()` after. Watch it refuse the
call that would cross the line:

```bash
python examples/02_cost.py
```

---

## 5. Reliability — survive a flaky provider

Real model APIs return 429s under load, 503s during incidents, and sometimes just
hang. [prod/reliability.py](prod/reliability.py) turns those *transient* failures
into a successful answer when possible and a clean, fast failure when not, with
three classic patterns:

- **Retry with exponential backoff + jitter** — wait a bit longer each time, with
  randomness so a thousand clients don't retry in lockstep.
- **Fallback** — when retries are exhausted, serve a cheaper model or a safe
  canned answer instead of a 500.
- **Circuit breaker** — after repeated failures, stop calling for a cooldown so a
  retry storm can't bury a recovering provider.

The mock fails on command, so you can watch all three work:

```bash
python examples/03_reliability.py
```

---

## 6. Caching — don't pay twice for the same answer

Model calls are the slow, expensive part. A repeat question should be a dictionary
lookup, not another call. The subtlety is the **key**: an answer is only reusable
if everything that shaped it — the question, the system prompt, *and the prompt
version* — is the same. [prod/cache.py](prod/cache.py) hashes all of it, so a
prompt change correctly invalidates the cache instead of serving a stale answer
(the same discipline the RAG repo used for its index cache).

```bash
python examples/04_caching.py
```

---

## 7. Guardrails — check what comes in and what goes out

The [prompt-injection deep dive](https://github.com/Ailuue/prompt-injection-deep-dive)
built these defenses one demo at a time. Production's job is to put them *on the
request path*: an **input guard** before the model (reject injection attempts and
pasted secrets) and an **output guard** after it (catch a leaked system prompt,
redact PII — but not your own published support address). Each returns a decision
the app records in the trace, so you can prove what was blocked and why.

```bash
python examples/05_guardrails.py
```

These are the "necessary, not sufficient" layer from repo #7 — cheap checks on
every request, backed by the capability limits and dual-LLM patterns taught there.

> 💡 **PII is a three-touchpoint problem, not one check.** Decide what you may send
> *upstream* to the provider at all (and under what retention), redact it on the
> way *out* (the output guard here — see the support-email allowlist in
> [prod/guardrails.py](prod/guardrails.py)), and keep it out of your *logs*
> (Section 3). Detecting it on the way *in* reuses the input-filter techniques from
> the prompt-injection repo's [input detection](https://github.com/Ailuue/prompt-injection-deep-dive)
> section — the same machinery, pointed at personal data instead of attacks.

---

## 8. Prompt versioning — the prompt is code

In every teaching repo the system prompt was a string literal next to the call —
fine until someone "improves" it and quietly breaks a behavior nobody re-tested.
Here prompts live in [prompts/](prompts/), one file per version.
[prod/prompts.py](prod/prompts.py) loads them, so a rollout is a config flip
(`PROMPT_VERSION` in `.env`) and a rollback is a one-line revert. Run the same
question through v1 and v2 and watch the behavior change — v2 is constrained to
the help center and cites its source:

```bash
python examples/06_prompt_versioning.py
```

---

## 9. Eval gates — a change ships only if it passes

The [evals deep dive](https://github.com/Ailuue/evals-deep-dive) taught you to
*measure* a change. This is where the measurement gets teeth: a **gate**. Before a
new prompt, model, or config goes live, it has to clear a threshold on a fixed
gold set ([evals/gold.jsonl](evals/gold.jsonl)) — exactly like a failing test
blocking a merge. [prod/evals.py](prod/evals.py) scores any answer function and
returns a pass/fail you can turn into a CI exit code. The gold set requires a
citation, so v1 fails the gate and v2 passes:

```bash
python examples/07_eval_gate.py        # exits non-zero if nothing clears the bar
```

---

## 10. The capstone: `serve.py`

Now the whole stack runs as one operable service. First, see all seven layers act
on a handful of requests — a live answer, a cache hit, a blocked injection, a
redaction — each with its trace:

```bash
python examples/08_app_end_to_end.py
```

Then run the real thing. [hands_on/serve.py](hands_on/serve.py) wraps
[prod/app.py](prod/app.py) as a CLI, an interactive REPL, and a tiny HTTP server —
and prints an ops summary (total cost, cache hit rate, budget remaining, breaker
state) on exit:

```bash
# Answer one question, with its trace
python hands_on/serve.py "How do I reset my password?"

# Interactive — the cache and budget persist across turns
python hands_on/serve.py

# As an HTTP service (still offline on the mock provider)
python hands_on/serve.py --server --port 8099
#   curl -s localhost:8099/ask -d '{"question":"Can I get a refund?"}'
#   curl -s localhost:8099/healthz
#   curl -s localhost:8099/metrics
```

It's a real, if small, production service: every request is traced, costed,
guarded, cached, and served from a versioned prompt that passed the gate. Flip
`PROVIDER` in `.env` and the same service runs against a real model — nothing else
changes.

---

## Going further — three more production concerns

The capstone covers the core seven layers. These three are the next ones you hit at
scale — and, like everything here, they run **offline on the mock**.

### Semantic caching
The exact-match cache (§6) misses every paraphrase. A **semantic cache** serves a
cached answer when a new query is close enough *in meaning* (embedding similarity) —
a much higher hit rate on real traffic, at the cost of a threshold you must tune so
you never serve a similar-but-wrong answer.
```bash
python examples/09_semantic_caching.py
```

### Model failover & cost routing
A model is a dependency: have a **backup** for when the primary is down (failover to
a cheaper model or a canned answer beats a 500), and **route** easy questions to a
cheap model while reserving the expensive one for the hard ones — same quality where
it matters, a fraction of the bill.
```bash
python examples/10_model_fallback.py
```

### Rate limiting & the feedback flywheel
A per-tenant **token bucket** stops one client from starving a shared, costly backend
(fairness, cost control, multi-tenancy). And capturing 👍/👎 on answers turns
production into your best eval set — the thumbs-down cases are exactly what to add as
regression tests (the evals dive) and fine-tuning data.
```bash
python examples/11_rate_limiting_and_feedback.py
```

---

## Where to go next

You've operated one app end to end. The road to a real deployment is mostly about
swapping each from-scratch layer for its industrial counterpart — the interfaces
stay the same:

- **Observability** → OpenTelemetry + a backend (Honeycomb, Datadog, Grafana,
  Langfuse), plus alerting on the metrics you're already emitting.
- **Cost** → per-customer/endpoint budgets in a real store, billing exports, and
  spend alerts; semantic caching to push the hit rate up.
- **Reliability** → a shared circuit-breaker/queue, provider failover, and load
  shedding under pressure.
- **Caching** → Redis (shared across servers, survives restarts) and an
  embeddings-based semantic cache for near-duplicate questions.
- **Guardrails** → a dedicated moderation/PII service and an LLM classifier on
  top of the rules here.
- **Prompts & evals** → a prompt registry with staged rollouts, and the eval gate
  wired into CI on every pull request, with LLM-as-judge scorers from the evals
  repo.
- **Where the model runs** → the `mock`/`openai`/`claude` swap in
  [prod/providers.py](prod/providers.py) is the same seam a `local` provider would
  use. Self-hosting an open-weight model (vLLM, Ollama, llama.cpp) trades the
  per-token bill and data-leaves-your-VPC concern for ops you now own — GPU
  capacity, batching, latency, and uptime. Every layer in this repo applies
  unchanged; you've just added a provider whose reliability is your problem too.

Each one slots on top of the same idea you started with: the model call is one
line; production is making that line safe, cheap, observable, and reliable.

---

## File map

```
check_setup.py              ← run first: verifies Python, packages, provider
README.md                   ← this guide
EXERCISES.md                ← predict-then-run prompts, one per section
prod/                       ← the from-scratch production stack (read it!)
  providers.py              ← the ONLY provider file: mock (default) + openai + claude
  observability.py          ← traces, spans, structured logs
  cost.py                   ← tokens -> dollars, plus an enforceable budget
  reliability.py            ← retries, backoff, fallback, circuit breaker
  cache.py                  ← TTL response cache keyed on everything that matters
  guardrails.py             ← input/output checks on the request path
  prompts.py                ← versioned prompts loaded from prompts/*.txt
  evals.py                  ← the gate: score an answer fn against the gold set
  app.py                    ← the one app: answer(question) through every layer
prompts/
  v1.txt, v2.txt            ← versioned system prompts (the gate decides which ships)
evals/
  gold.jsonl                ← the gold dataset the eval gate scores against
hands_on/
  serve.py                  ← capstone: CLI + REPL + HTTP server, with an ops summary
examples/
  00_mock_provider.py       ← the offline model that makes it all runnable (no key)
  01_observability.py       ← trace, spans, structured logs
  02_cost.py                ← cost accounting + a budget that refuses to overspend
  03_reliability.py         ← retry, fallback, circuit breaker (mock fails on cue)
  04_caching.py             ← cache hits, and why the prompt version is in the key
  05_guardrails.py          ← input/output checks, including PII redaction
  06_prompt_versioning.py   ← same question, v1 vs v2 behavior
  07_eval_gate.py           ← score both versions; only the passing one ships
  08_app_end_to_end.py      ← all seven layers on one request, with traces
  09_semantic_caching.py    ← cache by meaning (embedding similarity), not exact text
  10_model_fallback.py      ← failover to a backup model + cost routing by difficulty
  11_rate_limiting_and_feedback.py ← per-tenant token bucket + the 👍/👎 feedback flywheel
```

---

## Troubleshooting

Run `python check_setup.py` first — it catches most problems. Then, by symptom:

| What you see | What it means / the fix |
|--------------|-------------------------|
| `ModuleNotFoundError: dotenv` | Dependencies aren't installed or the venv isn't active. `source .venv/bin/activate` then `pip install -r requirements.txt`. |
| `PROVIDER=... needs ... in the environment` | You switched to a real provider without a key. Load it from your keychain with `secrun` (see [SECRETS.md](../SECRETS.md)), or go back to `PROVIDER=mock`. |
| The eval gate exits non-zero | That's the gate working — a version failed. `python examples/07_eval_gate.py` shows which case and why. |
| `BudgetExceeded` | The spend ceiling did its job. Raise it with `--budget` on the capstone, or `Budget(limit_usd=...)` in code. |
| Structured logs clutter my output | Logs go to **stderr**, answers to **stdout** — `python ... 2>/dev/null` hides logs. Or raise the level with `observability.set_level("error")`. |
| `circuit open — failing fast` | Expected after repeated failures (e.g. the reliability demo). The breaker reopens after its cooldown; `reset_mock_behavior()` clears injected faults. |
| `SyntaxError` / odd type errors on startup | You're likely on Python 3.9 or older; this repo needs 3.10+. `check_setup.py` confirms your version. |

Still stuck? Every file is small and self-contained — open it, read the docstring
at the top, and run it directly.

---

## The series

This is one of thirteen standalone, hands-on deep dives into building with LLM APIs — eight core, plus five bonus dives.
Each one stands on its own — its own setup, examples, and capstone — and they all
share the same house style: provider-agnostic, built from scratch (no
frameworks), offline-first examples, and a real capstone. Do them in any order;
this sequence builds naturally:

1. [OpenAI API](https://github.com/Ailuue/openai-api-deep-dive) — the API from zero
2. [Claude API](https://github.com/Ailuue/claude-api-deep-dive) — the same ideas, the Anthropic way
3. [Prompt Engineering](https://github.com/Ailuue/prompt-engineering-deep-dive) — shape model behavior with better prompts (zero/few-shot, chain-of-thought, roles)
4. [RAG](https://github.com/Ailuue/rag-deep-dive) — answer questions over your own documents
5. [Evals](https://github.com/Ailuue/evals-deep-dive) — measure whether a change actually helps
6. [Agents](https://github.com/Ailuue/agents-deep-dive) — give a model tools and a loop so it can act
7. [Prompt Injection & Guardrails](https://github.com/Ailuue/prompt-injection-deep-dive) — attack and defend all of the above
8. [Production](https://github.com/Ailuue/ai-in-production-deep-dive) — operate one app end to end: observability, cost, reliability, caching, guardrails, prompt versioning, eval gates

**Bonus dives** — standalone, slotting in where they're most useful:

- [Context Engineering](https://github.com/Ailuue/context-engineering-deep-dive) — manage what's in the window: memory, compaction, assembly
- [Multimodal](https://github.com/Ailuue/multimodal-deep-dive) — images & audio, not just text
- [Fine-tuning](https://github.com/Ailuue/fine-tuning-deep-dive) — teach a model new behavior by example
- [MCP](https://github.com/Ailuue/mcp-deep-dive) — serve tools, data & prompts to any LLM over a standard protocol
- [Local Models](https://github.com/Ailuue/local-models-deep-dive) — run open-weight models on your own machine
- [Agent Harnesses](https://github.com/Ailuue/agent-harness-deep-dive) — build on the loop: hooks, permissions, sandboxing, subagents
- [Realtime Voice](https://github.com/Ailuue/realtime-voice-deep-dive) — low-latency speech-to-speech agents

**You are here: #8 — Production.**
