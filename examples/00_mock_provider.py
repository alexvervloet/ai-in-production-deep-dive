#!/usr/bin/env python3
"""
00_mock_provider.py: the offline model that makes this whole repo runnable.

    python examples/00_mock_provider.py            # offline, no key

Every other repo in the series needs an API key to do anything interesting. This
one doesn't, because PROVIDER=mock is a real (if tiny) in-process "model": it
answers from a built-in support knowledge base, deterministically, and reports
token counts and latency just like a real provider. That's what lets us
demonstrate cost, retries, caching, and eval gates with no key and no network.

Run it, then flip PROVIDER=openai or PROVIDER=claude in .env to point the exact
same code at a real model. A real provider needs an API key, and the key lives in
your OS keychain (not .env), so you launch it through `secrun`, which injects the
key for that one command:

    secrun python examples/00_mock_provider.py     # PROVIDER=openai/claude

Flip the provider but forget `secrun`, and the key won't be on the environment 
so instead of crashing, the code degrades to the offline mock and says so, both in
a stderr banner and in the "Active provider: mock (FALLBACK: ...)" line below. That
keeps you running, but it is NOT the real model: use `secrun` for that, or set
PROVIDER_STRICT=1 to turn the missing key back into a hard error. One-time keychain
setup is in ../SECRETS.md.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from prod import providers

load_dotenv()

print(f"Active provider: {providers.describe()}\n")

SYSTEM = "You are the Acme Cloud support assistant."
for question in [
    "How do I reset my password?",
    "Can I get a refund?",
    "Do you support quantum entanglement?",  # not in the KB -> safe fallback
]:
    resp = providers.generate(SYSTEM, question)
    print(f"Q: {question}")
    print(f"A: {resp.text}")
    print(
        f"   [model={resp.model}  tokens={resp.prompt_tokens}+{resp.completion_tokens}"
        f"  latency={resp.latency_ms:.0f}ms]\n"
    )

print("Deterministic: run it again and every answer + token count is identical.")
print("That determinism is what makes the cost and eval demos reproducible offline.")
