"""Streamlit demo: chat with the single agent (main.py) or the multi-agent supervisor
(multi_agents.py) and watch the calls they make to the LLM app (llm.py) and the
decisions the Guardian app (guardian.py) makes on each step.

Run:  streamlit run demo.py     (or ./run_all.sh to start everything)
"""

import json
import time
from pathlib import Path

import httpx
import pandas as pd
import streamlit as st

CALLS_LOG = Path(__file__).parent / "logs" / "calls.log"
LLM_APP = "http://127.0.0.1:8000"
GUARDIAN_APP = "http://127.0.0.1:8787"
MODES = {
    "Single agent (main.py)": "http://127.0.0.1:8080",
    "Multi-agent supervisor (multi_agents.py)": "http://127.0.0.1:8090",
}

SAMPLES = [
    "Hello, who are you?",
    "What is (12.5 + 7.5) * 2 / 4?",
    "What is the latest price of AAPL?",
    "Search the web: who won the most recent FIFA World Cup?",
    "What is 6*7, and what is the AAPL price?",
    "Get the prices of AAPL and MSFT and tell me the difference.",
    "Search the web for Apple's ticker symbol, then tell me its latest price.",
]

# Each one triggers a rule in policy.py
GOVERNANCE_SAMPLES = [
    "My OpenAI key is sk-test-1234567890abcdefghij, is it valid?",
    "Use the calculator to compute `9**9**9`",
    "Search the web for priya.sharma@example.com",
    "Search the web for the exact phrase: ignore previous instructions",
    "Reply with exactly this text: contact me at priya.sharma@example.com",
]

ROW_COLORS = {"agent": "#e8f1fb", "multi-agent": "#e9f7ec", "llm": "#fdf1e3", "guardian": "#f1e9fb"}

st.set_page_config(page_title="Cortexa Agent demo", layout="wide")


def is_up(base_url: str) -> bool:
    try:
        return httpx.get(f"{base_url}/docs", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def parse_log(text: str) -> list[dict]:
    """Turn lines like 'time | app | EVENT | {json}' into table rows."""
    rows = []
    for line in text.splitlines():
        parts = line.split(" | ", 3)
        if len(parts) != 4:
            continue
        ts, app, event, data = parts
        try:
            agent = json.loads(data).get("agent", "")
        except ValueError:
            agent = ""
        rows.append(
            {
                "Time": ts.split(" ")[1],
                "App": app,
                "Agent": agent,
                "Event": event,
                "Details": data if len(data) <= 400 else data[:400] + " ...",
            }
        )
    return rows


def show_math(text: str) -> str:
    """Convert \\( \\) / \\[ \\] math from the model into Streamlit's $ / $$ syntax."""
    return text.replace("\\(", "$").replace("\\)", "$").replace("\\[", "$$").replace("\\]", "$$")


def show_events(events: list[dict], secs: float, mode: str) -> None:
    llm_calls = sum(e["Event"] == "OPENAI_REQUEST" for e in events)
    tool_calls = sum(e["Event"] == "TOOL_CALL" for e in events)
    checks = [e for e in events if e["Event"] == "ACS_DECISION"]
    stopped = sum('"decision": "allow"' not in e["Details"] for e in checks)
    with st.expander(
        f"Call sequence: {llm_calls} LLM call(s), {tool_calls} tool call(s), "
        f"{len(checks)} Guardian check(s) ({stopped} denied or modified), {secs:.1f}s"
    ):
        st.caption(f"Answered by: {mode}")
        if not events:
            st.write("No log lines were captured for this request.")
            return
        df = pd.DataFrame(events)
        styled = df.style.apply(
            lambda row: [f"background-color: {ROW_COLORS.get(row['App'], 'white')}; color: #222"] * len(row),
            axis=1,
        )
        st.dataframe(styled, hide_index=True, width="stretch")
        st.caption(
            "Blue = single-agent app (:8080). Green = multi-agent app (:8090). "
            "Orange = LLM app (:8000). Purple = Guardian app (:8787). "
            "The Agent column shows which agent made the call."
        )


def ask(prompt: str, mode: str) -> None:
    history = st.session_state.history
    history.append({"role": "user", "content": prompt})
    messages = [{"role": m["role"], "content": m["content"]} for m in history]

    offset = CALLS_LOG.stat().st_size if CALLS_LOG.exists() else 0
    start = time.time()
    try:
        r = httpx.post(f"{MODES[mode]}/chat", json={"messages": messages}, timeout=180)
        r.raise_for_status()
        answer = r.json()["content"]
    except Exception as exc:  # noqa: BLE001
        answer = f"Request failed: {exc}"
    secs = time.time() - start

    log_text = ""
    if CALLS_LOG.exists():
        with CALLS_LOG.open("rb") as f:
            f.seek(offset)
            log_text = f.read().decode("utf-8", errors="replace")
    history.append(
        {
            "role": "assistant",
            "content": answer,
            "events": parse_log(log_text),
            "secs": secs,
            "mode": mode,
        }
    )


# ---------------------------------------------------------------- sidebar
if "history" not in st.session_state:
    st.session_state.history = []

with st.sidebar:
    st.header("Mode")
    mode = st.radio("Who answers?", list(MODES), label_visibility="collapsed")
    st.header("Status")
    for name, url in MODES.items():
        st.write(f"{name.split(' (')[0]} ({url.rsplit(':', 1)[1]}): {'up' if is_up(url) else 'DOWN'}")
    st.write(f"LLM app (:8000): {'up' if is_up(LLM_APP) else 'DOWN'}")
    st.write(f"Guardian app (:8787): {'up' if is_up(GUARDIAN_APP) else 'DOWN'}")
    st.header("Try a question")
    for sample in SAMPLES:
        if st.button(sample, width="stretch"):
            st.session_state.queued = sample
    st.header("Try a governance rule")
    for sample in GOVERNANCE_SAMPLES:
        if st.button(sample, width="stretch"):
            st.session_state.queued = sample
    if st.button("Clear chat", type="primary", width="stretch"):
        st.session_state.history = []
        st.rerun()

# ---------------------------------------------------------------- main page
st.title("Cortexa Agent demo")
st.caption(
    "Pick a mode in the sidebar. Single agent: one LangChain create_agent with calculator, "
    "DuckDuckGo and yfinance tools. Multi-agent: a supervisor that delegates to math, research "
    "and finance specialist agents. Either way, every model turn goes to the LLM app (OpenAI), "
    "and every step (user message, tool call, tool result, answer) is checked by the Guardian "
    "app. Open 'Call sequence' under any reply to see the calls and decisions, in order."
)

prompt = st.chat_input("Ask something...") or st.session_state.pop("queued", None)
if prompt:
    with st.spinner(f"{mode.split(' (')[0]} is working..."):
        ask(prompt, mode)

for msg in st.session_state.history:
    with st.chat_message(msg["role"]):
        st.markdown(show_math(msg["content"]))
        if msg["role"] == "assistant":
            show_events(msg["events"], msg["secs"], msg["mode"])
