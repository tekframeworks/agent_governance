from fastapi import FastAPI
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from acs_middleware import ACSMiddleware
from call_log import get_logger, log_event
from call_logger import CallLogger
from tools import TOOLS

logger = get_logger("agent")

llm = ChatOpenAI(
    model="local-llm",
    base_url="http://127.0.0.1:8000/v1",
    api_key="not-needed",
)

# ACSMiddleware sends every step to the Guardian app (guardian.py) and applies its decision
agent = create_agent(model=llm, tools=TOOLS, middleware=[ACSMiddleware(logger)])

app = FastAPI()


class ChatRequest(BaseModel):
    messages: list[dict]  # [{"role": "user", "content": "..."}]


@app.post("/chat")
async def chat(req: ChatRequest):
    log_event(logger, "CHAT_REQUEST", messages=req.messages)
    result = await agent.ainvoke(
        {"messages": req.messages}, config={"callbacks": [CallLogger(logger)]}
    )
    final = result["messages"][-1]
    log_event(logger, "CHAT_RESPONSE", content=final.content)
    return final.model_dump()
