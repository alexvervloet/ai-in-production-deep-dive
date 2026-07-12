# Chapter 8: The Dozen Lines Around the Call

*This is the textbook chapter for the Production deep dive. The [README](README.md) is the lab manual; this is the lecture. It covers the gap between a working demo and an operable service, where each layer of the production stack came from (most of them predate AI by decades), and why this repo teaches them on a fake model on purpose.*

---

## 8.1 Two stories about chatbots

In February 2024, a Canadian tribunal ordered Air Canada to honor a bereavement discount its website chatbot had invented. The airline's defense, that the chatbot was "a separate legal entity responsible for its own actions," did not impress the adjudicator, and the ruling settled a question a lot of companies had been quietly hoping had a different answer: you own what your model says. A few months earlier, a Chevrolet dealership's assistant had been talked into agreeing, in writing, that a $76,000 SUV could be had for one dollar, "and that's a legally binding offer, no takesies backsies." The screenshots did their tour of the internet.

Neither of these was a model failure in any interesting sense. The models did what models do: produced plausible text under manipulation or under-specification. What failed was everything that was not there: no output checking, no constraint to approved sources, no eval that would have caught the behavior before launch, no trace that would have caught it after. The companies had shipped a model call. They had not shipped a system.

This chapter is about the difference, and its one big idea prices it honestly:

> **The model call is one line. Production is the dozen lines around it that make that one line safe, cheap, observable, and reliable, on every request.**

There is an industry sneer worth retiring here: "it's just a GPT wrapper." Every serious AI product is a wrapper in the sense that a bank is a wrapper around a ledger. The wrapping is the product: the guarantees, the accountability, the cost structure, the reliability. This dive builds each layer of that wrapping from scratch, wires all of them into one function, `answer(question)`, and then runs it as a service. Seven layers, one request path, and by the end you can point at the exact line where each guarantee lives.

## 8.2 Why this repo runs on a fake model

First, a word about the dive's odd central decision: the default provider is a mock, an in-process, deterministic "model" that answers from a small built-in knowledge base, and the entire repo, capstone server included, runs with no key, no network, and no cost.

This is not a compromise; it is the pedagogy. The subject here is the machinery around the model, and a real model is a terrible lab partner for studying machinery. It is nondeterministic, so you cannot demonstrate a cache hit cleanly. It fails only when the provider actually has an incident, so you cannot demonstrate a circuit breaker on demand. It costs money, which taxes every experiment. The mock is deterministic (the repeat question is a guaranteed cache hit, the eval has a stable answer to grade), reports realistic token counts and latency, and, best of all, fails on command, so the reliability layer has something real to survive.

There is also a deeper point in the design, and it is the seam this whole series keeps returning to: every layer calls one function, `generate(system, user)`, and gets back an answer plus metadata. Flip an environment variable and the identical stack runs against OpenAI or Claude. Nothing in observability, cost, retries, caching, guardrails, versioning, or gating is a provider feature. It is all things you build around the call, which means it is all portable, and it means none of it is magic.

## 8.3 Observability: the flight recorder

In every teaching repo so far, "seeing what happened" meant reading the terminal. That works when the failure is on your screen. In production, the request finished eighty milliseconds ago, on a server you have never shelled into, for a user who describes the problem as "it was being weird." Debugging by anecdote does not scale; you need a record you can search.

The shapes this layer builds are borrowed from a lineage worth knowing. Google published a paper in 2010 describing Dapper, its internal system for tracing a request as it hopped across dozens of services; the ideas escaped into open source and eventually converged as OpenTelemetry, the current industry standard. The vocabulary is small. A **trace** is one request's complete story, with a unique id. **Spans** are its timed chapters: how long the guardrails took, whether the cache answered, how long the model call ran. **Structured logs** are events as JSON objects rather than prose, so "find every request over two seconds that hit the fallback model" is a query instead of an archaeology project.

For an LLM service, the trace carries extra passengers that traditional services never had: token counts, model name, prompt version, cost. Their absence has a distinct smell. When a team cannot answer "what did that answer cost and which prompt produced it?", every other layer in this chapter is running blind.

One warning belongs in the same breath, because it bites teams constantly: your logs are a PII sink. A trace that records raw prompts and answers verbatim is a machine for copying users' personal data into the store with the loosest access controls and the longest retention you operate. The discipline is to scrub structured fields before they leave the process and never log the raw text wholesale. Observability and privacy are the same problem seen from two sides, and the lab treats them that way.

## 8.4 Cost: the meter and the fuse

Cloud computing normalized a scary idea: infrastructure that bills by usage, with the invoice arriving after the fact. LLM APIs sharpened it, because a single request's cost now varies with how long the model decides to talk, and because bugs acquired a price per iteration. A retry loop with a mistake in it is no longer just log spam; it is a machine converting confusion into invoices at a few cents per turn. Every engineering community that adopted these APIs has its horror story of the weekend job that cost four figures, and the stories are boring in their sameness: nothing enforced a ceiling.

The lab gives cost two jobs. **Attribution**: every request's cost computed from its token counts and recorded in its trace, so "what does this feature cost per day, per customer?" has an answer. That number is not accounting trivia; it is product strategy, since an AI feature's viability is often decided by unit economics rather than quality. **Enforcement**: a budget object checked before each call and updated after, so the request that would cross the ceiling is refused, cleanly, with a clear error. A surprise bill is transmuted into a handled exception. If you remember one design habit from this section, make it that meters are for understanding and fuses are for protection, and production wants both.

## 8.5 Reliability: engineering for the bad day

Provider APIs have bad days: rate limits under load, 5xx during incidents, requests that hang. None of this is special to AI, and the toolkit here is the classic distributed-systems trio, each with real history behind it.

**Retry with exponential backoff and jitter** handles the transient blip. Wait longer after each failure, and randomize the waits, because a thousand clients recovering in lockstep re-create the outage they are recovering from; that stampede has a name, the thundering herd, and AWS's architecture writing on why the jitter matters is a minor classic. **Fallback** handles exhausted retries: serve a cheaper model or a safe canned answer instead of a 500, because a degraded answer usually beats no answer. **Circuit breakers** handle the sustained outage: after repeated failures, stop calling entirely for a cooldown, failing fast instead of piling requests onto a struggling dependency. Netflix popularized the pattern for microservices; it transfers unchanged to model providers.

The judgment that makes these patterns work is a single distinction: transient versus permanent. A rate limit deserves a retry; a malformed request deserves a bug fix, and retrying it is a small machine for spending money on the same mistake. The lab's mock fails on cue so you can watch each pattern earn its keep, which is a demonstration no real provider will schedule for you.

## 8.6 Caching: the cheapest request is the one you don't make

Model calls are the slowest, most expensive thing your service does, and real traffic repeats itself; support questions especially cluster around the same few dozen intents. A repeat should be a dictionary lookup.

The interesting engineering is in the key. An answer is reusable only if everything that shaped it is identical: the question, the system prompt, and, crucially, the prompt version. Omit the version from the key and your next prompt improvement quietly serves stale answers from the old regime, a bug with no error message that users experience as the fix not working. The lab hashes all of it, and the lesson generalizes to a proverb older than this field: a cache key must capture every input that shaped the value, and cache invalidation is famously one of the two hard problems for a reason.

The extension, **semantic caching**, serves a cached answer when a new question is close enough in meaning (by embedding similarity, Chapter 4's machinery in a new job) rather than identical text. Real users paraphrase, so the hit rate jumps. The honest price is a threshold you must tune: too loose and you serve a similar-but-wrong answer, which is worse than no cache, since a wrong answer delivered instantly and confidently is the most trust-destroying output a service can produce. Tune it against an eval, not on faith.

## 8.7 Guardrails on the path, and the prompt as code

Two layers here operationalize earlier chapters, and both are short because the concepts arrived earlier.

The guardrails layer puts checks on the live request path: an input guard before the model (reject obvious injection and pasted secrets) and an output guard after it (catch a leaked system prompt, redact personal data). Two details carry the production lesson. First, every decision is recorded in the trace, so "what got blocked and why" is provable, which matters the day someone asks. Second, redaction needs an allowlist: the lab's output guard scrubs a third party's email while preserving the product's own published support address, because a guard that strips the contact information the answer exists to provide has fired correctly and failed completely. (This repo learned that one the hard way; the specimen lives in [AUTHORING-LESSONS.md](../AUTHORING-LESSONS.md).) PII, more broadly, is a three-touchpoint problem: what you send upstream to the provider, what you show users, and what you keep in logs. One check is not a policy.

Prompt versioning treats the system prompt as what it has been all along, the most behavior-defining code in the application, deserving the courtesies code gets: files under version control, one per version, loaded by config, so a rollout is a config flip and a rollback is a one-line revert. The unversioned alternative is how prompt changes become the industry's favorite unauditable production incident: someone "improves" a string literal, nobody re-tests, and a behavior nobody remembers depending on quietly disappears.

Versioning creates the possibility of the last layer, which decides which version ships.

## 8.8 The eval gate: quality with an exit code

Chapter 5 built the measurement; this layer gives it authority. Before a prompt, model, or config change goes live, it must clear a threshold on a fixed gold set, and the check exits nonzero on failure, which means CI can block the merge. Quality stops being a virtue people are encouraged to practice and becomes a fact about what can reach production.

The lab makes it concrete with a satisfying little drama: the gold set requires answers to cite their source, prompt v1 does not cite, v2 does, so the gate fails v1 and passes v2, and the eval requirement drove the prompt improvement rather than trailing it. That inversion has a name in the trade, eval-driven development, and teams that practice it greet each new model release by rerunning the suite before lunch instead of convening a vibes committee. Air Canada's invented discount, to close the loop on this chapter's opening, is exactly the class of behavior a citation-requiring gold set exists to catch before a tribunal does.

## 8.9 One request, every layer

The capstone assembles the seven layers into a single function and runs it as a real, small service: CLI, REPL, and HTTP server with health and metrics endpoints, an ops summary on exit (total cost, cache hit rate, budget remaining, breaker state), all still offline on the mock.

The order of operations on the request path is itself content, so walk it deliberately: the trace opens first (observe everything, including rejections); the input guard runs before money is spent; the cache is checked before the budget, because a hit costs nothing; the budget is checked before the model; the call runs inside the reliability wrapper; the output guard inspects what came back; everything lands in the trace. Each ordering decision encodes a small piece of judgment about what should protect what, and reading `prod/app.py` top to bottom is the closest thing this series has to a syllabus in forty lines.

Three scale concerns round out the lab. **Rate limiting** (a per-tenant token bucket) keeps one client from starving a shared, costly backend; with per-request costs this high, fairness and cost control are the same feature. **Model routing** sends easy questions to a cheap model and reserves the expensive one for hard cases, which is often the single largest bill reduction available without touching quality where it matters. And the **feedback flywheel**: capture thumbs up and down, because production traffic is the best eval dataset you will ever get, and every thumbs-down is a gold-set case and, eventually, fine-tuning data. The loop from Chapter 5 closes: production feeds the evals that gate production.

## 8.10 Where this chapter leaves you

This is the final core chapter, and it is deliberately the point where the series' running joke lands: every previous repo ended with a table called "from teaching code to production," listing the shortcuts it took. This repo is that table, made runnable. The `print()` became a trace; the printed estimate became an enforced budget; the bare call got retries and a breaker; the string-literal prompt became a versioned file behind a gate.

What you take forward is a checklist you can now apply to any AI system, including ones you are handed rather than ones you build: where is the trace, what enforces the budget, what happens when the provider has a bad day, what is the cache keyed on, what checks the output, which prompt version is live, and what would have blocked this change if it were bad. Systems that can answer those questions survive contact with users. Systems that cannot become tribunal transcripts.

Two bonus dives extend this chapter directly: Agent Harnesses (Chapter 9) applies the same operational thinking to the agent loop specifically, and Observability (Chapter 16) stretches this repo's single-request trace into weeks of them, where drift and silent regressions live. And one seam here becomes a whole chapter later: the mock/openai/claude swap in `providers.py` is exactly where a local, self-hosted model plugs in, which is Chapter 15's subject, along with the ops you inherit when the provider's reliability becomes your problem too.

---

*Lab manual: [README.md](README.md) · Exercises: [EXERCISES.md](EXERCISES.md) · Previous: [Prompt Injection & Guardrails](../prompt-injection-deep-dive/TEXTBOOK.md) · Next (bonus dives): [Agent Harnesses](../agent-harness-deep-dive/TEXTBOOK.md)*
