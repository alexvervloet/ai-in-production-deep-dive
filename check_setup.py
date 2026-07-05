"""
Setup check — run this first.
=============================

    python check_setup.py

It answers one question: "Is my environment ready?" It checks your Python
version, the installed packages, your chosen PROVIDER, and (only if you picked a
real provider) the API key it needs. It makes NO API calls, so it costs nothing.

The good news: the default PROVIDER=mock needs no key and only python-dotenv, so
"ready" is the common case here — the whole repo runs offline.

Uses only Python's standard library, so it runs even before `pip install`.
"""

import importlib.util
import os
import sys

_USE_COLOR = sys.stdout.isatty() and os.getenv("NO_COLOR") is None


def _c(text, code):
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def ok(msg):
    print(f"  {_c('✓', '32')} {msg}")


def warn(msg):
    print(f"  {_c('!', '33')} {msg}")


def fail(msg):
    print(f"  {_c('✗', '31')} {msg}")


HERE = os.path.dirname(os.path.abspath(__file__))


def _read_env_file():
    """Parse .env without needing python-dotenv to be installed yet."""
    env_path = os.path.join(HERE, ".env")
    values = {}
    if not os.path.exists(env_path):
        return None
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def _get(env, name):
    """Effective value of a var: real environment wins over the .env file."""
    return os.getenv(name) or (env or {}).get(name, "")


# Always needed. Provider SDKs are checked only for the real (non-mock) stacks.
ALWAYS = [("dotenv", "python-dotenv", "loads PROVIDER/config from .env")]
PROVIDER_DEPS = {
    "mock": [],  # the offline default: standard library only
    "openai": [("openai", "openai", "OpenAI chat")],
    "claude": [("anthropic", "anthropic", "Claude chat model")],
}
PROVIDER_KEYS = {
    "mock": [],
    "openai": [("OPENAI_API_KEY", "sk-", "sk-your-openai-key-here")],
    "claude": [("ANTHROPIC_API_KEY", "sk-ant-", "sk-ant-your-key-here")],
}


def check_python():
    print("Python version")
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 10):
        ok(f"Python {major}.{minor} (3.10+ required)")
        return True
    fail(f"Python {major}.{minor} — this repo needs Python 3.10 or newer.")
    print("    Install a newer Python from https://www.python.org/downloads/")
    return False


def check_provider(env):
    print("\nProvider")
    provider = (_get(env, "PROVIDER") or "mock").strip().lower()
    if provider in PROVIDER_DEPS:
        ok(f"PROVIDER = {provider}" + ("  (offline, no key needed)" if provider == "mock" else ""))
        return provider
    fail(f"PROVIDER = {provider!r} is not recognized.")
    print("    Set PROVIDER=mock (default), openai, or claude in .env.")
    return None


def check_dependencies(provider):
    print("\nDependencies")
    needed = ALWAYS + PROVIDER_DEPS.get(provider, [])
    missing = []
    for import_name, pip_name, purpose in needed:
        if importlib.util.find_spec(import_name) is not None:
            ok(f"{pip_name} — {purpose}")
        else:
            fail(f"{pip_name} MISSING — {purpose}")
            missing.append(pip_name)
    if provider == "mock":
        ok("everything else is the Python standard library")
    if missing:
        print("\n    Install everything with:")
        print("        pip install -r requirements.txt")
    return not missing


def check_keys(env, provider):
    print("\nAPI key(s)")
    keys = PROVIDER_KEYS.get(provider, [])
    if not keys:
        ok("none required for this provider")
        return True
    if env is None:
        fail(".env file not found.")
        print("    Create it with:  cp .env.example .env  (or just use PROVIDER=mock)")
        return False
    all_ok = True
    for name, prefix, placeholder in keys:
        value = _get(env, name)
        if not value or value == placeholder:
            fail(f"{name} is not set.")
            print("    Store it in your keychain + run under `secrun` (see SECRETS.md) — or set PROVIDER=mock to run offline.")
            all_ok = False
        elif not value.startswith(prefix):
            warn(f"{name} is set but doesn't start with '{prefix}'. Double-check it.")
        else:
            ok(f"{name} is set and looks right.")
    return all_ok


def main():
    print(_c("Checking your setup for the production deep dive...\n", "1"))
    env = _read_env_file()
    py = check_python()
    provider = check_provider(env)
    if provider is None:
        print(_c("\nFix PROVIDER in .env, then run this again.", "1;31"))
        return 1
    deps = check_dependencies(provider)
    keys = check_keys(env, provider)

    print()
    if py and deps and keys:
        print(_c("All set! 🎉", "1;32"))
        print("Start here:  python examples/00_mock_provider.py")
        print("Then the capstone:  python hands_on/serve.py \"How do I reset my password?\"")
        if provider == "mock":
            print("(Everything runs offline — no key, no cost.)")
        return 0
    print(_c("Not ready yet — fix the ✗ items above, then run this again.", "1;31"))
    print("Tip: PROVIDER=mock needs no key and runs the entire repo offline.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
