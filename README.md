# Production: A Guided Deep Dive

A hands-on playground for the part the other repos skipped: everything that wraps the
model call once real users depend on it. You'll take one small app, a customer-support
assistant, and operate it end to end, building each production concern from scratch.
Observability, cost control, reliability, caching, guardrails, prompt versioning, and eval
gates. No framework, no platform, no SaaS dashboard. Just enough code to see how each one
works.

Here is the thing that makes this repo work. It runs completely offline on a mock
provider, with no API key. The subject is the machinery around the model, so we ship a
tiny deterministic "model" in-process and wrap it in the real ops stack. Every example,
the eval gate, and the capstone server run with zero keys and zero cost. Flip one env var
and the exact same pipeline runs against a real OpenAI or Claude model.

This is the eighth and final core repo in the series. The first seven teach the pieces:
[the API](https://github.com/alexvervloet/openai-api-deep-dive),
[Claude](https://github.com/alexvervloet/claude-api-deep-dive),
[prompt engineering](https://github.com/alexvervloet/prompt-engineering-deep-dive),
[RAG](https://github.com/alexvervloet/rag-deep-dive),
[evals](https://github.com/alexvervloet/evals-deep-dive),
[agents](https://github.com/alexvervloet/agents-deep-dive), and
[guardrails](https://github.com/alexvervloet/prompt-injection-deep-dive). Each of those
ends with a section called "From teaching code to production." This repo is that section,
made runnable.

Like its siblings, walk through it rather than reading it. Each section ends with
something to run. Do the running. That is where the learning is. And
[EXERCISES.md](EXERCISES.md) has a predict-then-run prompt for each section.

---

## 0. The one big idea

A prototype answers the question. A production system answers the question and can also
tell you what it cost, prove what it did, survive the provider having a bad day, refuse to
overspend, avoid getting jailbroken, and not regress when someone edits the prompt. All of
that has the same shape.

> **The model call is one line. Production is the dozen lines around it that make
> that one line safe, cheap, observable, and reliable, on every request.**

Each section below is one of those concerns, built on its own, then wired into a single
`answer(question)` function in [prod/app.py](prod/app.py). That function is the whole
repo. One request, every layer, in order.

---

## 1. Setup (5 minutes)

```bash
# 1. Create an isolated Python environment
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 2. Install dependencies (just python-dotenv for the default offline stack)
pip install -r requirements.txt

# 3. Copy the env file: the default runs keyless (no API key needed)
cp .env.example .env
#    (Real provider instead of the mock? Its key goes in your OS keychain,
#     not .env: see ../docs/SECRETS.md, then run scripts as `secrun python ...`.)

# 4. Confirm everything is wired up (makes no API call, costs nothing)
python check_setup.py
```

That's it, and no key is required. The default `PROVIDER=mock` is a real in-process model
that answers from a built-in support knowledge base. Pick your stack with `PROVIDER` in
`.env`.

| `PROVIDER` | What runs the model | Keys needed | Cost |
|------------|---------------------|-------------|------|
| `mock` (default) | a deterministic offline "model" | **none** | **$0** |
| `openai` | OpenAI `gpt-5.4-nano` | `OPENAI_API_KEY` | tiny |
| `claude` | Claude `claude-haiku-4-5` | `ANTHROPIC_API_KEY` | tiny |

The production stack is identical on all three, and the only file that knows which one
you picked is [prod/providers.py](prod/providers.py). That is the whole point.
Observability, cost, retries, caching, guardrails, prompt versioning, and eval gates are
not provider features. They are things you build around the call.

> **Everything in this repo runs offline.** No key, no network, no cost, and that is what
> lets us demonstrate cost dashboards, retries, and eval gates with a model that fails,
> and succeeds, exactly when we tell it to.

---

## 2. The app, and the mock that powers it

The thing we're operating is a support assistant for a fictional product, Acme Cloud. Ask
it a question and it answers from a small knowledge base. That's the prototype every
sibling repo would call done.

The mock provider in [prod/providers.py](prod/providers.py) makes it operable offline. It
is deterministic. The same question always yields the same answer and the same token
counts, which is exactly what lets us demonstrate caching, where the repeat is a hit,
evals, which need a stable answer to grade, and cost, which needs token counts you can
predict. It also reports latency and can be told to fail on purpose, so the reliability
layer has something real to handle.

```bash
python examples/00_mock_provider.py
```

Every layer below calls `providers.generate(system, user)` and gets back an
`LLMResponse`, which holds the answer plus the metadata production code needs: token
counts, the model name, latency. Real providers fill those from the API. The mock computes
them locally.

---

## 3. Seeing what it actually did

In the teaching repos, observability was a `print()`. That works when the failure is on
your screen. In production the request finished 80ms ago, for someone else, and "it was
slow" is all you get. You need a record you can search.

[prod/observability.py](prod/observability.py) builds three things from the standard
library. A **trace**, one object per request with a unique id. **Spans**, timed sections
for guardrails, cache, and the model call, so you can see where the time went. And
**structured logs**, one JSON object per event, filterable by trace id, latency, or error.
It is a teaching-sized OpenTelemetry: same shapes, no backend.

```bash
python examples/01_observability.py
```

> **Your logs are a PII sink.** A trace records request fields, so naive logging is the
> fastest way to leak the exact data your output guard in Section 7 just redacted. It ends
> up in plaintext in a log store that usually has looser access controls and longer
> retention than your database. Apply the same discipline as the output guard. Scrub
> structured fields before they leave the process, and never log a raw prompt or answer
> verbatim. Observability and PII are the same problem seen from two sides.

---

## 4. Turning tokens into dollars, and refusing to overspend

The API repos taught you to estimate cost. Production gives that estimate two jobs.
Attribution records what every request cost, tagged with its trace id. Enforcement is a
budget the app won't blow through. A runaway loop or an abuse spike should hit a ceiling
and stop, rather than showing up as a surprise invoice.

[prod/cost.py](prod/cost.py) prices a call from its token counts and exposes a `Budget`
you `check()` before spending and `record()` after. Watch it refuse the call that would
cross the line.

```bash
python examples/02_cost.py
```

---

## 5. Surviving a flaky provider

Real model APIs return 429s under load, 503s during incidents, and sometimes they just
hang. [prod/reliability.py](prod/reliability.py) turns those transient failures into a
successful answer when possible and a clean, fast failure when not, using three classic
patterns.

- **Retry with exponential backoff and jitter.** Wait a bit longer each time, with
  randomness so a thousand clients don't retry in lockstep.
- **Fallback.** When retries are exhausted, serve a cheaper model or a safe canned answer
  instead of a 500.
- **Circuit breaker.** After repeated failures, stop calling for a cooldown so a retry
  storm can't bury a recovering provider.

The mock fails on command, so you can watch all three work.

```bash
python examples/03_reliability.py
```

---

## 6. Don't pay twice for the same answer

Model calls are the slow, expensive part. A repeat question should be a dictionary lookup
rather than another call. The subtlety is the key. An answer is only reusable if
everything that shaped it stayed the same: the question, the system prompt, and the prompt
version. [prod/cache.py](prod/cache.py) hashes all of it, so a prompt change correctly
invalidates the cache instead of serving a stale answer. Same discipline the RAG repo used
for its index cache.

```bash
python examples/04_caching.py
```

---

## 7. Checking what comes in and what goes out

The [prompt-injection deep dive](https://github.com/alexvervloet/prompt-injection-deep-dive)
built these defenses one demo at a time. Production's job is to put them on the request
path. An input guard before the model rejects injection attempts and pasted secrets. An
output guard after it catches a leaked system prompt and redacts PII, while leaving your
own published support address alone. Each one returns a decision the app records in the
trace, so you can prove what was blocked and why.

```bash
python examples/05_guardrails.py
```

These are the necessary-but-not-sufficient layer from repo #7. Cheap checks on every
request, backed by the capability limits and dual-LLM patterns taught there.

> **PII is a three-touchpoint problem rather than one check.** Decide what you may send
> *upstream* to the provider at all (and under what retention), redact it on the
> way *out* (the output guard here; see the support-email allowlist in
> [prod/guardrails.py](prod/guardrails.py)), and keep it out of your *logs*
> (Section 3). Detecting it on the way *in* reuses the input-filter techniques from
> the prompt-injection repo's [input detection](https://github.com/alexvervloet/prompt-injection-deep-dive)
> section, the same machinery pointed at personal data instead of attacks.

---

## 8. The prompt is code

In every teaching repo the system prompt was a string literal next to the call. That is
fine until someone "improves" it and breaks a behavior nobody re-tested. Here prompts live
in [prompts/](prompts/), one file per version. [prod/prompts.py](prod/prompts.py) loads
them, so a rollout is a config flip (`PROMPT_VERSION` in `.env`) and a rollback is a
one-line revert. Run the same question through v1 and v2 and watch the behavior change.
v2 is constrained to the help center and cites its source.

```bash
python examples/06_prompt_versioning.py
```

---

## 9. A change ships only if it passes

The [evals deep dive](https://github.com/alexvervloet/evals-deep-dive) taught you to
measure a change. This is where the measurement gets teeth, in the form of a gate. Before
a new prompt, model, or config goes live it has to clear a threshold on a fixed gold set,
[evals/gold.jsonl](evals/gold.jsonl), exactly like a failing test blocking a merge.
[prod/evals.py](prod/evals.py) scores any answer function and returns a pass or fail you
can turn into a CI exit code. The gold set requires a citation, so v1 fails the gate and
v2 passes.

```bash
python examples/07_eval_gate.py        # exits non-zero if nothing clears the bar
```

A threshold on a fixed gold set is the right first gate and it has a limit worth knowing
about. It compares one score against one number, so it cannot tell a real regression from
a run that landed differently. [model-swap](https://github.com/alexvervloet/model-swap)
gates the same kind of change on paired per-case outcomes and an interval instead, which
turns "87% versus 89%" into ship, do not ship, or inconclusive with the number of extra
cases that would settle it.

---

## 10. The capstone: `serve.py`

Now the whole stack runs as one operable service. First, watch all seven layers act on a
handful of requests. A live answer, a cache hit, a blocked injection, a redaction, each
one with its trace.

```bash
python examples/08_app_end_to_end.py
```

Then run the real thing. [hands_on/serve.py](hands_on/serve.py) wraps
[prod/app.py](prod/app.py) as a CLI, an interactive REPL, and a tiny HTTP server, and
prints an ops summary on exit with total cost, cache hit rate, budget remaining, and
breaker state.

```bash
# Answer one question, with its trace
python hands_on/serve.py "How do I reset my password?"

# Interactive: the cache and budget persist across turns
python hands_on/serve.py

# As an HTTP service (still offline on the mock provider)
python hands_on/serve.py --server --port 8099
#   curl -s localhost:8099/ask -d '{"question":"Can I get a refund?"}'
#   curl -s localhost:8099/healthz
#   curl -s localhost:8099/metrics
```

It is a real production service, if a small one. Every request gets traced, costed,
guarded, cached, and served from a versioned prompt that passed the gate. Flip `PROVIDER`
in `.env` and the same service runs against a real model. The only other thing that
changes is the key. A real provider needs one, it lives in your keychain rather than
`.env`, so you launch through `secrun python ...`. See
[SECRETS.md](../docs/SECRETS.md). The application code stays untouched.

---

## Going further: three more production concerns

The capstone covers the core seven layers. These three are the next ones you hit at
scale, and like everything here they run offline on the mock.

### Semantic caching
The exact-match cache in §6 misses every paraphrase. A semantic cache serves a cached
answer when a new query is close enough in meaning, by embedding similarity. That means a
much higher hit rate on real traffic, at the cost of a threshold you have to tune so you
never serve a similar-but-wrong answer.
```bash
python examples/09_semantic_caching.py
```

### Model failover and cost routing
A model is a dependency, so keep a backup for when the primary is down. Failing over to a
cheaper model or a canned answer beats a 500. And route easy questions to a cheap model
while reserving the expensive one for the hard ones. Same quality where it matters, a
fraction of the bill.
```bash
python examples/10_model_fallback.py
```

### Rate limiting and the feedback loop
A per-tenant token bucket stops one client from starving a shared, costly backend, which
covers fairness, cost control, and multi-tenancy at once. And capturing thumbs up and down
on answers turns production into your best eval set. The thumbs-down cases are exactly
what to add as regression tests, per the evals dive, and as fine-tuning data.
```bash
python examples/11_rate_limiting_and_feedback.py
```

---

## Where to go next

You've operated one app end to end. The road to a real deployment is mostly swapping each
from-scratch layer for its industrial counterpart. The interfaces stay the same.

- **Observability** → OpenTelemetry + a backend (Honeycomb, Datadog, Grafana,
  Langfuse), plus alerting on the metrics you're already emitting. This repo traces
  *one request*; watching *weeks* of them (input/quality drift, silent regressions,
  and alerting that doesn't cry wolf) is its own bonus dive,
  [**Observability**](https://github.com/alexvervloet/observability-deep-dive), built
  on the same trace/log shapes you emit here.
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
- **Where the model runs** → the `mock`, `openai`, and `claude` swap in
  [prod/providers.py](prod/providers.py) is the same join a `local` provider would use.
  Self-hosting an open-weight model with vLLM, Ollama, or llama.cpp trades the per-token
  bill and the data-leaves-your-VPC concern for ops you now own: GPU capacity, batching,
  latency, uptime. Every layer in this repo applies unchanged. You have added a provider
  whose reliability is your problem too.

Every one of these sits on top of the idea you started with. The model call is one line,
and production is making that line safe, cheap, observable, and reliable.

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
  11_rate_limiting_and_feedback.py ← per-tenant token bucket + the thumbs up/down feedback loop
```

---

## Troubleshooting

Run `python check_setup.py` first. It catches most problems. Then, by symptom:

| What you see | What it means / the fix |
|--------------|-------------------------|
| `ModuleNotFoundError: dotenv` | Dependencies aren't installed or the venv isn't active. `source .venv/bin/activate` then `pip install -r requirements.txt`. |
| `PROVIDER=... needs ... in the environment` | You switched to a real provider without a key. Load it from your keychain with `secrun` (see [SECRETS.md](../docs/SECRETS.md)), or go back to `PROVIDER=mock`. |
| The eval gate exits non-zero | That's the gate working: a version failed. `python examples/07_eval_gate.py` shows which case and why. |
| `BudgetExceeded` | The spend ceiling did its job. Raise it with `--budget` on the capstone, or `Budget(limit_usd=...)` in code. |
| Structured logs clutter my output | Logs go to **stderr**, answers to **stdout**, so `python ... 2>/dev/null` hides logs. Or raise the level with `observability.set_level("error")`. |
| `circuit open, failing fast` | Expected after repeated failures (e.g. the reliability demo). The breaker reopens after its cooldown; `reset_mock_behavior()` clears injected faults. |
| `SyntaxError` / odd type errors on startup | You're likely on Python 3.9 or older; this repo needs 3.10+. `check_setup.py` confirms your version. |

Still stuck? Every file is small and self-contained. Open it, read the docstring
at the top, and run it directly.

---

## The series

This is one of the standalone, hands-on deep dives into building with LLM APIs. Eight
core dives, plus the bonus ones listed below. Each one stands on its own, with its own
setup, examples, and capstone, and they all share one house style. Provider-agnostic,
built from scratch with no frameworks, offline-first examples, and a real capstone at
the end. Do them in any order. This sequence builds naturally.

1. [OpenAI API](https://github.com/alexvervloet/openai-api-deep-dive): the API from zero
2. [Claude API](https://github.com/alexvervloet/claude-api-deep-dive): the same ideas, the Anthropic way
3. [Prompt Engineering](https://github.com/alexvervloet/prompt-engineering-deep-dive): shape model behavior with better prompts, using zero-shot and few-shot, chain-of-thought, and roles
4. [RAG](https://github.com/alexvervloet/rag-deep-dive): answer questions over your own documents
5. [Evals](https://github.com/alexvervloet/evals-deep-dive): measure whether a change actually helps
6. [Agents](https://github.com/alexvervloet/agents-deep-dive): give a model tools and a loop so it can act
7. [Prompt Injection & Guardrails](https://github.com/alexvervloet/prompt-injection-deep-dive): attack and defend all of the above
8. [Production](https://github.com/alexvervloet/ai-in-production-deep-dive): operate one app end to end, across observability, cost, reliability, caching, guardrails, prompt versioning, and eval gates

**Bonus dives**, standalone and slotting in where they're most useful:

- [Context Engineering](https://github.com/alexvervloet/context-engineering-deep-dive): manage what's in the window, with memory, compaction, and assembly
- [AI Data Engineering](https://github.com/alexvervloet/ai-data-engineering-deep-dive): the corpus behind the index, with versions, lineage, ACLs, and deletes
- [Multimodal](https://github.com/alexvervloet/multimodal-deep-dive): images and audio as well as text
- [Fine-tuning](https://github.com/alexvervloet/fine-tuning-deep-dive): teach a model new behavior by example
- [MCP](https://github.com/alexvervloet/mcp-deep-dive): serve tools, data, and prompts to any LLM over a standard protocol
- [Local Models](https://github.com/alexvervloet/local-models-deep-dive): run open-weight models on your own machine
- [Agent Harnesses](https://github.com/alexvervloet/agent-harness-deep-dive): build on the loop, adding hooks, permissions, sandboxing, and subagents
- [Realtime Voice](https://github.com/alexvervloet/realtime-voice-deep-dive): low-latency speech-to-speech agents
- [Observability](https://github.com/alexvervloet/observability-deep-dive): watch a running app over time, covering drift, quality, alerting, and the feedback loop
- [Architecture](https://github.com/alexvervloet/architecture-deep-dive): the seams between the components, each decision measured rather than asserted
- [GenAI Security](https://github.com/alexvervloet/genai-security-deep-dive): treat the model as an untrusted principal, and put identity, supply chain, isolation, budgets, and release gates around it
- [Inference Platform Engineering](https://github.com/alexvervloet/inference-platform-deep-dive): turn finite GPU memory and a request queue into latency, throughput, and a fleet size you can defend
- [Testing & Delivery](https://github.com/alexvervloet/testing-and-delivery-deep-dive): decide whether a build is fit to promote, using evidence, gates, staged rollout, and rollback
- [Professional Tools](https://github.com/alexvervloet/professional-tools-deep-dive): rebuild each hand-written piece with the tool professionals reach for, and measure both

And the whole series lands in one codebase in the
[capstone](https://github.com/alexvervloet/deep-dive-capstone): a codebase Q&A tool
built step by step, one tag per dive.

**You are here: #8, Production.**
