#!/usr/bin/env python3
"""
serve.py — the capstone: run the support assistant as a real service.
=====================================================================

Everything in the repo comes together here as one operable app. It exposes the
full production pipeline (observability, cost, reliability, caching, guardrails,
prompt versioning) two ways, and prints an ops summary on exit so you can see
what the "fleet" did: total cost, cache hit rate, budget remaining.

It runs offline on the mock provider by default — no key, no network.

Examples
--------
  # Ask one question and see the answer + its trace
  python hands_on/serve.py "How do I reset my password?"

  # Interactive REPL — keep asking; the cache and budget persist across turns
  python hands_on/serve.py

  # Run an HTTP server and curl it (still offline on the mock provider)
  python hands_on/serve.py --server --port 8099
  #   curl -s localhost:8099/ask -d '{"question":"Can I get a refund?"}'
  #   curl -s localhost:8099/healthz
  #   curl -s localhost:8099/metrics

  # Show the full structured trace for each request
  python hands_on/serve.py --trace "What plans do you offer?"

Flip PROVIDER in .env (mock | openai | claude) to point the same service at a
real model — nothing else changes.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from prod import observability as obs, prompts, providers
from prod.app import App


def ops_summary(app: App) -> dict:
    """The numbers an on-call engineer actually watches."""
    return {
        "provider": providers.provider_name(),
        "prompt_version": prompts.active_version(),
        "requests": app.budget.calls,
        "spent_usd": round(app.budget.spent_usd, 6),
        "budget_remaining_usd": round(app.budget.remaining_usd, 6),
        "cache_hit_rate": round(app.cache.hit_rate, 3),
        "breaker_state": app.breaker.state,
    }


def answer_once(app: App, question: str, show_trace: bool) -> None:
    ans = app.answer(question)
    tag = "blocked" if ans.blocked else ("cached" if ans.cached else "live")
    print(f"\n{ans.text}")
    print(f"\n[{tag}  trace={ans.trace_id}  prompt={ans.prompt_version}  ${ans.cost_usd:.6f}]")
    if show_trace:
        print(json.dumps(ans.trace_summary, indent=2, default=str))


def run_repl(app: App, show_trace: bool) -> None:
    print(f"Acme Cloud support assistant — provider={providers.describe()}")
    print("Ask a question (Ctrl-D or 'quit' to exit).\n")
    while True:
        try:
            question = input("you> ").strip()
        except EOFError:
            break
        if not question:
            continue
        if question.lower() in {"quit", "exit"}:
            break
        answer_once(app, question, show_trace)
    print("\n" + json.dumps({"ops_summary": ops_summary(app)}, indent=2))


def run_server(app: App, port: int) -> None:
    """A tiny stdlib HTTP server — no framework, same as the rest of the series."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if self.path == "/healthz":
                self._send(200, {"status": "ok", "provider": providers.provider_name()})
            elif self.path == "/metrics":
                self._send(200, ops_summary(app))
            else:
                self._send(404, {"error": "not found", "try": ["/ask", "/healthz", "/metrics"]})

        def do_POST(self):  # noqa: N802
            if self.path != "/ask":
                return self._send(404, {"error": "POST /ask"})
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                question = payload["question"]
            except (ValueError, KeyError):
                return self._send(400, {"error": 'body must be {"question": "..."}'})
            ans = app.answer(question)
            self._send(200, {
                "answer": ans.text, "trace_id": ans.trace_id, "cached": ans.cached,
                "blocked": ans.blocked, "cost_usd": round(ans.cost_usd, 6),
                "prompt_version": ans.prompt_version,
            })

        def log_message(self, *args):  # silence default access logs; we have traces
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Serving on http://127.0.0.1:{port}  (provider={providers.provider_name()})")
    print("  POST /ask {\"question\": \"...\"}   GET /healthz   GET /metrics")
    print("Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n" + json.dumps({"ops_summary": ops_summary(app)}, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the support assistant with the full production stack.")
    parser.add_argument("question", nargs="?", help="a single question to answer, then exit")
    parser.add_argument("--server", action="store_true", help="run an HTTP server instead of the CLI")
    parser.add_argument("--port", type=int, default=8099, help="port for --server (default 8099)")
    parser.add_argument("--trace", action="store_true", help="print the full structured trace per request")
    parser.add_argument("--budget", type=float, default=0.05, help="spend ceiling in USD (default 0.05)")
    args = parser.parse_args()

    load_dotenv()
    providers.ensure_ready()  # no-op for PROVIDER=mock; checks keys for real stacks
    obs.set_level("warning")  # keep stdout clean; traces still emit on warnings/errors

    app = App()
    app.budget.limit_usd = args.budget

    if args.server:
        run_server(app, args.port)
    elif args.question:
        answer_once(app, args.question, args.trace)
        print("\n" + json.dumps({"ops_summary": ops_summary(app)}, default=str))
    else:
        run_repl(app, args.trace)
    return 0


if __name__ == "__main__":
    sys.exit(main())
