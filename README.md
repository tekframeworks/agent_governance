# Cortexa Agent + ACS governance

The Cortexa agent demo, with every agent step checked by a Guardian that follows the
[OWASP Agent Control Standard (ACS)](https://github.com/GenAI-Security-Project/agent-control-standard).

Three small FastAPI apps work together:

- **Agent app** (`main.py`, port 8080): a LangChain `create_agent` with three tools (calculator, DuckDuckGo search, yfinance). It receives chat messages, decides when to call tools, runs them, and returns the final answer.
- **LLM app** (`llm.py`, port 8000): the model endpoint the agent calls. Right now it forwards requests to OpenAI. Later it can be swapped for your own hosted model.
- **Guardian app** (`guardian.py`, port 8787): before the agent acts, it sends the step here. The Guardian checks it against the rules in `policy.py` and answers allow, deny or modify. The agent does what it says.

```
client ──POST /chat──▶ Agent app (:8080) ──POST /v1/chat/completions──▶ LLM app (:8000) ──▶ OpenAI
                         │  create_agent runs the tools itself
                         ├─ calculator · ddg · yfinance_quote
                         └─ ACSMiddleware ──POST /acs (ACS envelopes)──▶ Guardian app (:8787)
                                                                           └─ policy.py, run by the
                                                                              agent-control-specification engine
```

`create_agent` handles the whole tool loop. When the model replies with a tool call, LangChain runs the tool, sends the result back to the model, and repeats until the model gives a final answer. There is no hand-written tool-execution code.

Governance is one extra argument: `create_agent(..., middleware=[ACSMiddleware(logger)])`. The middleware sends each step to the Guardian and applies the decision. The agent code does not change otherwise.

## ACS in one minute

ACS is a wire standard for controlling AI agents at run time. Before an agent does something, it sends the step to a separate **Guardian** and waits for a **decision**.

| ACS term | In this repo |
|----------|--------------|
| Observed Agent: the agent being governed | `main.py`, `multi_agents.py`, through `acs_middleware.py` |
| Guardian Agent: decides what the agent may do | `guardian.py` |
| Hook (step): a point where the agent asks | `steps/userMessage`, `steps/toolCallRequest`, `steps/toolCallResult`, `steps/agentResponse` |
| Decision | `allow`, `deny`, `modify` (ACS also defines `ask` and `defer`, see below) |
| Handshake | `handshake/hello` at the start of every session |
| Wire format | JSON-RPC 2.0 on `POST /acs` |

A real request and answer, when the model searches for an email address:

```json
{"jsonrpc": "2.0", "id": "5bf230e6-...", "method": "steps/toolCallRequest",
 "params": {"acs_version": "0.1.0", "request_id": "5bf230e6-...", "timestamp": "2026-10-27T13:56:13+00:00",
            "metadata": {"agent_id": "agent", "agent_name": "agent", "session_id": "285482da-..."},
            "payload": {"tool": {"name": "ddg"}, "arguments": {"query": {"value": "find a@b.com"}}}}}
```

```json
{"jsonrpc": "2.0", "id": "5bf230e6-...",
 "result": {"type": "final", "acs_version": "0.1.0", "request_id": "5bf230e6-...",
            "decision": "modify", "reason_codes": ["pii_removed"],
            "policy_references": [{"policy_id": "demo_rules", "rule_id": "pii_removed"}],
            "reasoning": "Email addresses were removed from the search query.",
            "modifications": {"parameter_overrides": {"query": "find [email]"}}}}
```

The agent then runs DuckDuckGo with `find [email]`.

## What is imported, and what is in this repo

ACS itself is a specification: JSON schemas and documents. There is no ACS package to install. The ACS project ships one Guardian, written in TypeScript for Bun, as its reference implementation.

That reference Guardian does not write its own policy engine. It runs Microsoft's Agent Governance Toolkit (AGT) engine, which is published on PyPI as **`agent-control-specification`**. This repo imports that package (see `requirements.txt`). No ACS or AGT files are copied in.

| Part | Where it comes from |
|------|---------------------|
| Policy engine: reads `manifest.yaml`, runs the rules, fails closed on errors | `pip install agent-control-specification` |
| ACS wire: build the envelopes, send them, apply the decisions | `acs_middleware.py` (agent side), `guardian.py` (Guardian side) |
| The rules | `policy.py` |
| Which steps are checked, and which tools exist | `manifest.yaml` |

Two names to keep apart, because both shorten to "ACS":

- **Agent Control Standard** (OWASP): the wire between agent and Guardian. Decisions: `allow`, `deny`, `modify`, `ask`, `defer`.
- **Agent Control Specification** (Microsoft AGT): the engine inside the Guardian. Verdicts: `allow`, `warn`, `deny`, `escalate`, `transform`.

`guardian.py` translates one into the other with the same table the ACS reference Guardian uses (`mapping.yaml` there):

| Engine verdict | ACS decision |
|----------------|--------------|
| `allow`, `warn` | `allow` |
| `deny` | `deny` |
| `transform` | `modify` |
| `escalate` | `ask`, then turned into `deny` (see "What this demo leaves out") |

## Project layout

| File | Purpose |
|------|---------|
| `main.py` | Agent app: `POST /chat`, `create_agent` with `ACSMiddleware`, and the callback that logs LLM and tool calls |
| `multi_agents.py` | Multi-agent app (`POST /chat` on port 8090): a supervisor agent plus math, research and finance specialist agents, each with its own `ACSMiddleware` |
| `acs_middleware.py` | Agent side of ACS: a LangChain middleware that sends each step to the Guardian and applies its decision |
| `guardian.py` | Guardian app: `POST /acs`, the handshake, and the four steps. Runs `policy.py` through the engine |
| `policy.py` | The governance rules, as plain Python |
| `manifest.yaml` | Engine configuration: which steps are checked, what the rules see, which tools exist |
| `call_logger.py` | LangChain callback that logs every LLM and tool call (shared by `main.py` and `multi_agents.py`) |
| `tools.py` | The three `@tool` definitions: `calculator`, `ddg`, `yfinance_quote` |
| `llm.py` | LLM app: `POST /v1/chat/completions`, forwards to OpenAI |
| `call_log.py` | Shared logger; all apps append to `logs/calls.log` |
| `demo.py` | Streamlit demo: chat page that also shows the call sequence and the Guardian's decisions |
| `run_all.sh` | Starts the LLM app, Guardian app, single-agent app, multi-agent app and Streamlit demo in one command |
| `run_tests.py` | Runs every message in `test_messages.md` and saves one log file per request in `logs/tests/` |
| `test_messages.md` | Messages to try against the agent, including one per governance rule |
| `.env.example` | Template for the environment variables |
| `requirements.txt` | Python dependencies |

## Setup

Needs Python 3.11 or newer on Linux x86_64. The `agent-control-specification` package on PyPI has a prebuilt wheel only for Linux x86_64. On Windows or macOS, pip falls back to the source package, which needs a Rust toolchain to build.

```bash
cd cortexa_agents_acs
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and set OPENAI_API_KEY
```

`.env` settings:

| Variable | Read by | Meaning | Default |
|----------|---------|---------|---------|
| `OPENAI_API_KEY` | LLM app | Your OpenAI key | none (required) |
| `OPENAI_MODEL` | LLM app | Model the LLM app uses | `gpt-4o-mini` |
| `ACS_ON_DECISION_FAILURE` | Guardian app | What agents do when the Guardian fails to answer: `deny` or `proceed` | `deny` |

The agent apps do not need a key. They talk only to the LLM app and the Guardian app.

## Run everything at once (with the Streamlit demo)

```bash
./run_all.sh
```

This starts the LLM app (`:8000`), the Guardian app (`:8787`), the single-agent app (`:8080`), the multi-agent app (`:8090`) and the demo page at `http://127.0.0.1:8501`. Press Ctrl+C to stop all of them. Each app's console output goes to `logs/llm_app.out`, `logs/guardian.out`, `logs/agent_app.out`, `logs/multi_agents.out` and `logs/demo.out`. The script refuses to start if `OPENAI_API_KEY` is missing from `.env`. If a leftover copy is still using one of the five ports, the script stops it first and then starts fresh. To only free the ports without starting anything, run `./run_all.sh stop`. This kills only processes you own on ports 8000, 8787, 8080, 8090 and 8501. Everything listens on `127.0.0.1` only, so to open the demo from your own computer, forward the port (for example `ssh -L 8501:127.0.0.1:8501 <server>`).

In the demo, the sidebar has a mode switch: **Single agent** sends your message to `main.py`, **Multi-agent supervisor** sends it to `multi_agents.py`. The sidebar also has one button per governance rule. The call-sequence table under each reply shows the Guardian's decisions as purple rows, between the agent's calls. The Agent column (agent, supervisor, math_agent, research_agent, finance_agent) shows whose step it was.

## Run the apps separately

Use three terminals, all with the virtualenv activated.

```bash
# Terminal 1: LLM app
uvicorn llm:app --host 127.0.0.1 --port 8000

# Terminal 2: Guardian app
uvicorn guardian:app --host 127.0.0.1 --port 8787

# Terminal 3: agent app
uvicorn main:app --host 127.0.0.1 --port 8080
```

If the Guardian is not running, the agent blocks every step, because the default failure posture is `deny` (see below).

If you see `address already in use`, an old copy is still running. Stop it with `fuser -k 8000/tcp` (or 8787, 8080).

None of the apps reload on code changes, so restart the one you edited. After editing `policy.py` or `manifest.yaml`, restart the Guardian app.

## Test

```bash
curl -s localhost:8080/chat -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"What is 6*7, and what is the AAPL price?"}]}'
```

The request body is `{"messages": [{"role": "...", "content": "..."}]}`, using the same roles as OpenAI chat (`system`, `user`, `assistant`). The response is the final assistant message from the agent. More example messages are in `test_messages.md`. Interactive docs are at `http://127.0.0.1:8080/docs`.

A message the Guardian blocks:

```bash
curl -s localhost:8080/chat -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"My OpenAI key is sk-test-1234567890abcdefghij, is it valid?"}]}'
```

The `content` of the reply is `Blocked by the Guardian (secret_in_prompt): The message contains an API key or password.` The LLM app is never called.

## Governance with ACS

### The steps, in order

A question that uses a tool looks like this in `logs/calls.log`:

```
agent CHAT_REQUEST
→ guardian ACS_HANDSHAKE                         session starts; the agent learns the failure posture
→ guardian ACS_DECISION  steps/userMessage       before the model sees the message
→ agent LLM_CALL → llm OPENAI_REQUEST → llm OPENAI_RESPONSE (tool_calls) → agent LLM_RESULT
→ guardian ACS_DECISION  steps/toolCallRequest   before the tool runs
→ agent TOOL_CALL → agent TOOL_RESULT
→ guardian ACS_DECISION  steps/toolCallResult    before the model sees the tool output
→ agent LLM_CALL → llm OPENAI_REQUEST → llm OPENAI_RESPONSE (answer) → agent LLM_RESULT
→ guardian ACS_DECISION  steps/agentResponse     before the answer leaves
→ agent CHAT_RESPONSE
```

### What the agent does with each decision

| Step | `deny` | `modify` |
|------|--------|----------|
| `steps/userMessage` | Replies with the Guardian's reason. The model is not called. | Uses the new message text |
| `steps/toolCallRequest` | The tool does not run. The model gets the reason as the tool result. | Runs the tool with the new arguments |
| `steps/toolCallResult` | The model gets the reason instead of the tool output | The model gets the redacted output |
| `steps/agentResponse` | The reply is the reason instead of the answer | The reply is the new text |

Any other decision (`ask`, `defer`, or a `modify` the agent cannot apply) is treated as `deny`, as ACS requires.

### The rules

All rules are in `check()` in `policy.py`.

| # | Step | Rule | Decision | Try it |
|---|------|------|----------|--------|
| 1 | `steps/userMessage` | The message contains an API key (`sk-...`, `AKIA...`) or `password: ...` | deny `secret_in_prompt` | My OpenAI key is sk-test-1234567890abcdefghij, is it valid? |
| 2 | `steps/toolCallRequest` | `calculator`: more than one `**`, or an exponent above 100 | deny `expression_too_expensive` | Use the calculator to compute `9**9**9` |
| 3 | `steps/toolCallRequest` | `ddg`: the query contains an email address. It is replaced by `[email]` | modify `pii_removed` | Search the web for priya.sharma@example.com |
| 4 | `steps/toolCallResult` | Any tool output that says "ignore previous instructions" or similar (prompt injection) | deny `prompt_injection` | Search the web for the exact phrase: ignore previous instructions |
| 5 | `steps/toolCallResult`, `steps/agentResponse` | Email addresses in a tool output or in the answer are replaced by `[email]` | modify `pii_masked` | Reply with exactly this text: contact me at priya.sharma@example.com |
| - | `steps/toolCallRequest` | A tool that is not listed in `manifest.yaml` | deny `runtime_error:tool_unknown` (from the engine, not `policy.py`) | Remove `ddg` from `manifest.yaml`, restart the Guardian, ask for a search |

Rule 2 protects the app: the calculator would try to compute `9**9**9` and freeze. Rule 4 depends on what DuckDuckGo returns, so it may not fire every time.

The engine also denies, with a `runtime_error:...` reason, when a rule raises an exception, returns something invalid, or when a step has no entry in `manifest.yaml`. It never lets a step through because something broke.

### Adding a rule or a tool

- **New rule**: add an `if` to `check()` in `policy.py` that returns `deny(...)` or `transform(...)`. Restart the Guardian.
- **New tool**: add the `@tool` to `tools.py` and the `TOOLS` list as before, and also add its name under `tools:` in `manifest.yaml`. Without that, the engine denies every call to it.

### When the Guardian does not answer

ACS calls this a decision failure: the Guardian is down, too slow (10 seconds here), or returns an error. The agent then applies the **failure posture**, which the Guardian sends in the handshake:

- `deny` (default here): the step is blocked with reason `guardian_unavailable`. Fail closed.
- `proceed`: the step goes ahead unchecked. Fail open.

Set it with `ACS_ON_DECISION_FAILURE` in `.env`. If the handshake itself fails, the agent cannot learn the posture, so it denies. Every failure is logged as `ACS_FAILURE` by the agent app.

Note: the ACS specification's default is `proceed`, and so is the reference Guardian's. This demo defaults to `deny`, because a governance demo that silently lets steps through when the Guardian is down would hide the point.

### Multi-agent

Every agent, the supervisor included, has its own `ACSMiddleware` and its own ACS session. The supervisor asking a specialist shows up as a `steps/toolCallRequest` for `ask_math_agent` (or research, finance). The specialist then gets that request as its own `steps/userMessage`. So in multi-agent mode one question gives several handshakes and several sessions, one per agent run. The `agent` field in each Guardian log line says whose step it was.

### What this demo leaves out

This is for understanding the flow. Like the ACS reference Guardian, it does not meet all of ACS-Core, the mandatory baseline:

| ACS-Core requirement | In this demo |
|----------------------|--------------|
| Handshake (`handshake/hello`) | Yes, at the start of each session. The Guardian checks only the ACS version. |
| JSON-RPC 2.0 envelopes | Yes. The envelopes were checked against the ACS v0.1.0 JSON schemas while building this. They are not validated at run time. |
| Hooks: `sessionStart`, `userMessage`, `toolCallRequest`, `toolCallResult`, `agentResponse`, `sessionEnd`, `subagentStart` | 4 of 7. No `sessionStart`, `sessionEnd` or `subagentStart`. Delegation to a specialist is checked as a tool call instead. |
| Decisions `allow`, `deny`, `ask`, `defer` (and `modify`) | `allow`, `deny`, `modify`. `ask` is turned into `deny` with `approver_unavailable`, as ACS section 9.2 requires for agents with no approval screen. `defer` is never produced. |
| Session audit chain (`chain_hash`) | No |
| Replay protection | No |
| Signed envelopes (HMAC-SHA256) | No. The wire is plain HTTP on `127.0.0.1`, so anything on the machine can call the Guardian. |
| Apply every decision; failure posture; log every failure | Yes |
| `system/ping` | No |

## Multi-agent app

`multi_agents.py` talks to the same LLM app as `main.py`, but with several agents:

```
client ──POST /chat──▶ supervisor ──▶ ask_math_agent     ──▶ math_agent     ──▶ calculator
                          │        ├─▶ ask_research_agent ──▶ research_agent ──▶ ddg
                          │        └─▶ ask_finance_agent  ──▶ finance_agent  ──▶ yfinance_quote
                          ├─ every agent calls the LLM app (:8000) for its model turns
                          └─ every agent sends its steps to the Guardian app (:8787)
```

Each specialist is a `create_agent` with its own tool. The supervisor is a `create_agent` whose tools are the specialists (each wrapped with `as_tool`), so `create_agent` runs the whole loop. The supervisor can ask several specialists at once, and they run in parallel. If a specialist fails, the supervisor gets the error as text, so the request does not crash.

```bash
uvicorn multi_agents:app --host 127.0.0.1 --port 8090   # LLM app and Guardian app must be running
curl -s localhost:8090/chat -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"What is 6*7, and what is the AAPL price?"}]}'
```

It logs to the same `logs/calls.log` (app name `multi-agent`). Log lines that come from an agent include `"agent": "<name>"`, so you can follow who did what. Answer quality depends on the model, and web-search answers can still be wrong when DuckDuckGo returns nothing useful.

## Tools

| Tool | What it does |
|------|--------------|
| `calculator` | Evaluates arithmetic: `+ - * / % **` and parentheses |
| `ddg` | Searches the web with DuckDuckGo and returns the top 5 results |
| `yfinance_quote` | Returns last price, previous close and currency for a ticker |

To add a tool, write another `@tool` function in `tools.py`, add it to the `TOOLS` list, and add its name to `tools:` in `manifest.yaml`.

## Logging

All apps write to one file, `logs/calls.log`, one line per event, so the order of events across the apps is preserved.

```
2026-10-14 01:58:52.973 | agent | TOOL_CALL | {"tool": "calculator", "input": "{'expression': '6 * 7'}"}
```

| App | Events |
|-----|--------|
| `agent` | `CHAT_REQUEST`, `LLM_CALL`, `LLM_RESULT`, `TOOL_CALL`, `TOOL_RESULT`, `CHAT_RESPONSE`, `LLM_ERROR`, `TOOL_ERROR`, `ACS_FAILURE` |
| `multi-agent` | The same events as `agent` |
| `llm` | `OPENAI_REQUEST`, `OPENAI_RESPONSE`, `OPENAI_ERROR` |
| `guardian` | `ACS_HANDSHAKE`, `ACS_DECISION` (one per checked step: method, decision, reason codes, payload) |

Notes:
- Timestamps use the server's timezone (UTC on this machine).
- Messages, tool outputs and ACS payloads are logged in full, so lines can be long. This includes text the Guardian blocked, such as a secret in a user message.
- The file is never rotated; delete or truncate it when it gets big.

## Why the endpoint is `/v1/chat/completions`

The agent app calls the LLM app at `http://127.0.0.1:8000/v1/chat/completions`. We use this path, not a custom one like `/v1/chat`, for these reasons:

1. **LangChain's `ChatOpenAI` already calls it.** `ChatOpenAI` uses the OpenAI SDK, which always posts to `<base_url>/chat/completions`. With `base_url="http://127.0.0.1:8000/v1"` the final URL is `/v1/chat/completions`. Using `/v1/chat` would need custom HTTP code to rewrite the URL, which is the kind of extra code we removed.

2. **It is a standard API with a fixed format.** The OpenAI Chat Completions format defines how `messages`, `tools` and `tool_choice` go in, and how `choices[0].message.content` or `tool_calls` come back. `create_agent` depends on that format to detect a tool call and send the result back. Any server that follows it works with the agent unchanged.

3. **Your own model can drop in later.** Common local model servers (vLLM, Ollama, llama.cpp server, LM Studio) expose `/v1/chat/completions` in the same format. Replacing OpenAI with your hosted model means changing only `llm.py` (or pointing `base_url` in `main.py` at the model server directly). The agent code does not change.

4. **The LLM app is a drop-in stand-in for OpenAI.** The agent sends the same JSON it would send to OpenAI, and gets back the same JSON shape. That is what "send the request the same way and receive the response the same way as an LLM" needs.

If you need the path to be exactly `/v1/chat`, the simplest way is to add a second route in `llm.py` for that path. The agent would then need a custom HTTP transport to call it, so we recommend staying with `/v1/chat/completions`.

The Guardian link uses the same idea: `POST /acs` carries the standard ACS envelope, not a format made up for this repo.

## Replacing OpenAI with your own model

Only `llm.py` needs to change. Keep the route `POST /v1/chat/completions`, accept the OpenAI-format JSON (`messages`, `tools`, ...), and return an OpenAI-format response. When the model wants a tool, the response must contain `choices[0].message.tool_calls` with `finish_reason: "tool_calls"`. When it has the final answer, return plain `content` with `finish_reason: "stop"`. The agent runs the tools and calls the endpoint again as needed.
