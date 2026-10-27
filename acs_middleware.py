"""Agent side of ACS (the "Observed Agent"): a LangChain middleware that sends each step
to the Guardian app (guardian.py) and does what the Guardian decides.

    agent = create_agent(model=llm, tools=TOOLS, middleware=[ACSMiddleware(logger)])

Each agent run is one ACS session. It starts with a handshake, then sends these steps:

    ACS step                when                       deny                 modify
    steps/userMessage       before the agent starts    reply with reason    use the new text
    steps/toolCallRequest   before each tool runs      tool does not run    use the new arguments
    steps/toolCallResult    after each tool runs       output withheld      use the redacted output
    steps/agentResponse     before the answer leaves   answer withheld      use the new text

If the Guardian does not answer, the agent applies the failure posture the Guardian sent
in the handshake ("deny" or "proceed"). Before a handshake succeeds it denies.
"""

import uuid
from datetime import datetime, timezone

import httpx
from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from typing_extensions import NotRequired

from call_log import log_event

GUARDIAN_URL = "http://127.0.0.1:8787/acs"
ACS_VERSION = "0.1.0"

CLIENT_HELLO = {
    "acs_versions_supported": [ACS_VERSION],
    "methods_implemented": [
        "steps/userMessage",
        "steps/toolCallRequest",
        "steps/toolCallResult",
        "steps/agentResponse",
    ],
    "transports_supported": ["http"],
    "provenance_producer": "none",
}


class ACSState(AgentState):
    acs_session_id: NotRequired[str]
    acs_blocked: NotRequired[bool]  # True when the user message itself was blocked


def blocked(result: dict) -> str:
    """The text the agent shows instead of a blocked step."""
    codes = ", ".join(result.get("reason_codes", []))
    return f"Blocked by the Guardian ({codes}): {result.get('reasoning', '')}"


def text(value) -> list[dict]:
    """ACS sends text as content blocks."""
    return [{"type": "text", "value": value}]


class ACSMiddleware(AgentMiddleware):
    state_schema = ACSState

    def __init__(self, logger, agent_name: str = "agent"):
        super().__init__()
        self.logger = logger
        self.agent_name = agent_name
        self.on_failure = "deny"

    async def send(self, session_id: str, method: str, payload: dict) -> dict | None:
        """Send one ACS request to the Guardian. Returns its result, or None if it failed."""
        request_id = str(uuid.uuid4())
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": {
                "acs_version": ACS_VERSION,
                "request_id": request_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "metadata": {
                    "agent_id": self.agent_name,
                    "agent_name": self.agent_name,
                    "session_id": session_id,
                },
                "payload": payload,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:  # Guardian's timeout_config
                response = await client.post(GUARDIAN_URL, json=request)
            return response.json()["result"]
        except Exception as exc:  # noqa: BLE001 - down, slow, or an error instead of a decision
            log_event(
                self.logger,
                "ACS_FAILURE",
                agent=self.agent_name,
                method=method,
                error=repr(exc),
                posture=self.on_failure,
            )
            return None

    async def decide(self, session_id: str, method: str, payload: dict) -> dict:
        """Ask the Guardian about one step. With no answer, apply the failure posture."""
        result = await self.send(session_id, method, payload)
        if result is not None:
            return result
        if self.on_failure == "proceed":
            return {"decision": "allow"}
        return {
            "decision": "deny",
            "reason_codes": ["guardian_unavailable"],
            "reasoning": "The Guardian did not answer.",
        }

    # --- handshake + steps/userMessage ------------------------------------------------
    @hook_config(can_jump_to=["end"])
    async def abefore_agent(self, state, runtime):
        session_id = str(uuid.uuid4())
        hello = await self.send(session_id, "handshake/hello", CLIENT_HELLO)
        self.on_failure = hello.get("on_decision_failure", "proceed") if hello else "deny"

        message = state["messages"][-1]  # the newest user message
        result = await self.decide(session_id, "steps/userMessage", {"content": text(message.content)})
        changes = result.get("modifications", {})
        if result["decision"] == "allow":
            return {"acs_session_id": session_id}
        if result["decision"] == "modify" and "modified_content" in changes:
            new_message = HumanMessage(id=message.id, content=changes["modified_content"])
            return {"acs_session_id": session_id, "messages": [new_message]}
        # deny (or anything this agent cannot apply): answer with the reason, skip the model
        return {
            "acs_session_id": session_id,
            "acs_blocked": True,
            "messages": [AIMessage(blocked(result))],
            "jump_to": "end",
        }

    # --- steps/toolCallRequest + steps/toolCallResult ---------------------------------
    async def awrap_tool_call(self, request, handler):
        session_id = request.state["acs_session_id"]
        call = request.tool_call
        tool = {"name": call["name"]}

        arguments = {name: {"value": value} for name, value in call["args"].items()}
        payload = {"tool": tool, "arguments": arguments}
        result = await self.decide(session_id, "steps/toolCallRequest", payload)
        changes = result.get("modifications", {})
        if result["decision"] == "modify" and "parameter_overrides" in changes:
            new_args = {**call["args"], **changes["parameter_overrides"]}
            request = request.override(tool_call={**call, "args": new_args})
        elif result["decision"] != "allow":
            return ToolMessage(blocked(result), tool_call_id=call["id"], name=call["name"], status="error")

        output = await handler(request)  # runs the tool

        payload = {"tool": tool, "exit_status": "success", "outputs": [{"value": output.content}]}
        result = await self.decide(session_id, "steps/toolCallResult", payload)
        changes = result.get("modifications", {})
        if result["decision"] == "modify" and changes.get("redactions"):
            # the only redaction path we send is /outputs/0/value, i.e. the whole output
            return output.model_copy(update={"content": changes["redactions"][0].get("replacement", "")})
        if result["decision"] != "allow":
            return output.model_copy(update={"content": blocked(result)})
        return output

    # --- steps/agentResponse ------------------------------------------------------------
    async def aafter_agent(self, state, runtime):
        if state.get("acs_blocked"):
            return None  # the answer is already the Guardian's reason
        answer = state["messages"][-1]
        payload = {"content": text(answer.content)}
        result = await self.decide(state["acs_session_id"], "steps/agentResponse", payload)
        changes = result.get("modifications", {})
        if result["decision"] == "allow":
            return None
        if result["decision"] == "modify" and "modified_content" in changes:
            new_text = changes["modified_content"]
        else:
            new_text = blocked(result)
        return {"messages": [AIMessage(id=answer.id, content=new_text)]}  # same id = replace it
