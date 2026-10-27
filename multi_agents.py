"""Multi-agent app: a supervisor agent that delegates to specialist agents.

Every agent uses the same LLM app (llm.py) as main.py does. Each specialist is
wrapped as a tool, so the supervisor calls it like any other tool and
create_agent runs the whole loop. Every agent, the supervisor included, sends its
steps to the Guardian (guardian.py) through ACSMiddleware.

Run:  uvicorn multi_agents:app --host 127.0.0.1 --port 8090
"""

from fastapi import FastAPI
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from acs_middleware import ACSMiddleware
from call_log import get_logger, log_event
from call_logger import CallLogger
from tools import calculator, ddg, yfinance_quote

logger = get_logger("multi-agent")

llm = ChatOpenAI(
    model="local-llm",
    base_url="http://127.0.0.1:8000/v1",
    api_key="not-needed",
)

# --- specialist agents: each one has its own tool -------------------------------
math_agent = create_agent(
    model=llm,
    tools=[calculator],
    name="math_agent",
    middleware=[ACSMiddleware(logger, "math_agent")],
    system_prompt="You are a math specialist. Use the calculator tool for all arithmetic "
    "and reply with the result.",
)
research_agent = create_agent(
    model=llm,
    tools=[ddg],
    name="research_agent",
    middleware=[ACSMiddleware(logger, "research_agent")],
    system_prompt="You are a web research specialist. Use the ddg tool to find current "
    "facts and reply with a short summary. Your own knowledge may be out of date, so do "
    "not add years or dates to your search queries unless the user gave them, and "
    "report what the search results say.",
)
finance_agent = create_agent(
    model=llm,
    tools=[yfinance_quote],
    name="finance_agent",
    middleware=[ACSMiddleware(logger, "finance_agent")],
    system_prompt="You are a stock market specialist. Use the yfinance_quote tool to get "
    "prices and reply with the figures.",
)


def as_tool(agent, name: str, description: str):
    """Expose a specialist agent to the supervisor as a tool."""

    @tool(name, description=description)
    async def run(request: str) -> str:
        try:
            result = await agent.ainvoke({"messages": [{"role": "user", "content": request}]})
            return result["messages"][-1].content
        except Exception as exc:  # noqa: BLE001 - let the supervisor see the failure
            return f"{name} failed: {exc}"

    return run


# --- supervisor: decides which specialist(s) to ask ------------------------------
supervisor = create_agent(
    model=llm,
    tools=[
        as_tool(math_agent, "ask_math_agent", "Ask the math specialist to do calculations."),
        as_tool(research_agent, "ask_research_agent", "Ask the research specialist to search the web."),
        as_tool(finance_agent, "ask_finance_agent", "Ask the finance specialist for stock prices."),
    ],
    name="supervisor",
    middleware=[ACSMiddleware(logger, "supervisor")],
    system_prompt="You are a supervisor. Delegate work to your specialist agents, "
    "using several of them when a question needs it, then combine their answers into "
    "one final reply. Answer simple questions yourself.",
)

app = FastAPI()


class ChatRequest(BaseModel):
    messages: list[dict]  # [{"role": "user", "content": "..."}]


@app.post("/chat")
async def chat(req: ChatRequest):
    log_event(logger, "CHAT_REQUEST", messages=req.messages)
    result = await supervisor.ainvoke(
        {"messages": req.messages}, config={"callbacks": [CallLogger(logger)]}
    )
    final = result["messages"][-1]
    log_event(logger, "CHAT_RESPONSE", content=final.content)
    return final.model_dump()
