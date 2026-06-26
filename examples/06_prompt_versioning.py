#!/usr/bin/env python3
"""
06_prompt_versioning.py — the prompt is versioned code.
=======================================================

    python examples/06_prompt_versioning.py            # offline, no key

The system prompt lives in prompts/*.txt, one file per version. Here we load v1
and v2 and run the same question through each so you can *see* the behavior
change: v1 just answers; v2 is constrained to the help center and cites its
source. Because every version is a file, a rollout is a config flip
(PROMPT_VERSION in .env) and a rollback is a one-line revert — and the eval gate
(example 07) can score a new version before you promote it.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from prod import prompts, providers

load_dotenv()

Q = "Can I get a refund, and do you have a source for that?"
print(f"Available prompt versions: {prompts.available_versions()}")
print(f"Active (PROMPT_VERSION): {prompts.active_version()}\n")

for version in prompts.available_versions():
    system = prompts.get_prompt(version)
    resp = providers.generate(system, Q)
    print(f"[{version}] {resp.text}\n")

print("Same question, same model — only the versioned prompt changed. That's why")
print("the prompt belongs in version control and behind an eval gate, not inline.")
