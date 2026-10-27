import os

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from openai import AsyncOpenAI

from call_log import get_logger, log_event

load_dotenv()  # load OPENAI_API_KEY / OPENAI_MODEL from .env

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

client = AsyncOpenAI()  # reads OPENAI_API_KEY from the environment

logger = get_logger("llm")

app = FastAPI()


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    body["model"] = MODEL  # ignore the model name sent by the agent
    log_event(
        logger,
        "OPENAI_REQUEST",
        model=MODEL,
        messages=body.get("messages"),
        tools=[t["function"]["name"] for t in body.get("tools", [])],
    )
    try:
        response = await client.chat.completions.create(**body)
    except Exception as exc:
        log_event(logger, "OPENAI_ERROR", error=repr(exc))
        raise
    message = response.choices[0].message
    log_event(
        logger,
        "OPENAI_RESPONSE",
        content=message.content,
        tool_calls=[c.model_dump() for c in message.tool_calls or []],
        finish_reason=response.choices[0].finish_reason,
    )
    return response.model_dump()
