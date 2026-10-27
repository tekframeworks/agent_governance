"""Send every message in test_messages.md to the agent, one at a time, and save
the slice of logs/calls.log written by each request into its own file.

Usage: python run_tests.py      (both apps must be running)
Output: logs/tests/<run timestamp>/NN_<slug>.log  and  summary.txt
"""

import json
import re
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).parent
CALLS_LOG = ROOT / "logs" / "calls.log"
AGENT_URL = "http://127.0.0.1:8080/chat"

MULTI_TURN = [
    {"role": "user", "content": "What is the latest price of AAPL?"},
    {"role": "assistant", "content": "AAPL is trading at about $341."},
    {"role": "user", "content": "Multiply that by 50."},
]


def load_cases() -> list[tuple[str, list[dict]]]:
    """Single-message cases come from the bullets in test_messages.md."""
    cases = []
    text = (ROOT / "test_messages.md").read_text()
    text = text.split("## What to expect")[0]
    for line in text.splitlines():
        if line.startswith("- "):
            msg = line[2:].strip()
            cases.append((msg, [{"role": "user", "content": msg}]))
    cases.append(("Multi-turn: AAPL price, then multiply by 50", MULTI_TURN))
    return cases


def main() -> None:
    run_dir = ROOT / "logs" / "tests" / time.strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True)
    CALLS_LOG.parent.mkdir(exist_ok=True)
    CALLS_LOG.touch()

    summary = []
    cases = load_cases()
    for i, (title, messages) in enumerate(cases, start=1):
        start = CALLS_LOG.stat().st_size
        t0 = time.time()
        try:
            r = httpx.post(AGENT_URL, json={"messages": messages}, timeout=180)
            status = r.status_code
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
            answer = body.get("content") if isinstance(body, dict) and "content" in body else body
        except Exception as exc:  # noqa: BLE001
            status, answer = "ERROR", repr(exc)
        elapsed = time.time() - t0

        with CALLS_LOG.open("rb") as f:
            f.seek(start)
            log_lines = f.read().decode("utf-8", errors="replace")

        n_llm = log_lines.count("| OPENAI_REQUEST |")
        n_tools = log_lines.count("| TOOL_CALL |")
        n_errors = len(re.findall(r"\| (?:LLM_ERROR|TOOL_ERROR|OPENAI_ERROR) \|", log_lines))
        n_checks = log_lines.count("| ACS_DECISION |")
        n_stopped = n_checks - log_lines.count('"decision": "allow"')

        slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:50]
        out = run_dir / f"{i:02d}_{slug}.log"
        out.write_text(
            f"QUESTION: {title}\n"
            f"REQUEST:  {json.dumps(messages, ensure_ascii=False)}\n"
            f"STATUS:   {status}   TIME: {elapsed:.1f}s\n"
            f"ANSWER:   {answer}\n"
            f"\n--- calls.log lines for this request ---\n{log_lines}"
        )
        summary.append(
            f"{i:02d} | {status} | {elapsed:5.1f}s | llm_calls={n_llm} tool_calls={n_tools} "
            f"errors={n_errors} guardian_checks={n_checks} denied_or_modified={n_stopped} "
            f"| {title}  ->  {out.name}"
        )
        print(summary[-1])

    (run_dir / "summary.txt").write_text("\n".join(summary) + "\n")
    print(f"\nLogs saved in {run_dir}")


if __name__ == "__main__":
    main()
