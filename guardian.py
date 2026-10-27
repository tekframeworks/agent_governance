"""Guardian app: the Guardian Agent of the OWASP Agent Control Standard (ACS).

Before an agent acts, it sends the step to POST /acs as an ACS request (JSON-RPC 2.0).
The Guardian checks the step against the rules in policy.py and answers with an ACS
decision: allow, deny or modify. The rules run inside the Agent Control Specification
engine (pip package agent-control-specification), the policy engine that the ACS
reference Guardian uses. The engine reads manifest.yaml.

Run:  uvicorn guardian:app --host 127.0.0.1 --port 8787
"""

import os
from pathlib import Path

from agent_control_specification import AgentControl
from dotenv import load_dotenv
from fastapi import FastAPI

from call_log import get_logger, log_event
from policy import Rules

load_dotenv()  # ACS_ON_DECISION_FAILURE from .env

ACS_VERSION = "0.1.0"

# ACS step -> the engine's name for the same point (as in the ACS reference mapping.yaml)
POINTS = {
    "steps/userMessage": "input",
    "steps/toolCallRequest": "pre_tool_call",
    "steps/toolCallResult": "post_tool_call",
    "steps/agentResponse": "output",
}

# engine verdict -> ACS decision
DECISIONS = {"allow": "allow", "warn": "allow", "deny": "deny", "escalate": "ask", "transform": "modify"}

# Sent to each agent in the handshake
SERVER_HELLO = {
    "negotiated_version": ACS_VERSION,
    "methods_evaluated": list(POINTS),
    "selected_transport": "http",
    "timeout_config": {"default_ms": 10000},
    # what an agent does when the Guardian fails to answer: "deny" (block) or "proceed"
    "on_decision_failure": os.getenv("ACS_ON_DECISION_FAILURE", "deny"),
}

engine = AgentControl.from_path(str(Path(__file__).parent / "manifest.yaml"), policy_dispatcher=Rules())

logger = get_logger("guardian")

app = FastAPI()


def text_of(content: list[dict]) -> str:
    """ACS sends text as content blocks: [{"type": "text", "value": "..."}]."""
    return "\n".join(str(block["value"]) for block in content if block["type"] == "text")


def to_snapshot(method: str, payload: dict) -> dict:
    """Turn the ACS payload into the input the engine checks (paths are in manifest.yaml)."""
    if method == "steps/toolCallRequest":
        args = {name: arg["value"] for name, arg in payload["arguments"].items()}
        return {"tool_call": {"name": payload["tool"]["name"], "args": args}}
    if method == "steps/toolCallResult":
        return {"tool_call": {"name": payload["tool"]["name"]}, "tool_result": payload["outputs"][0]["value"]}
    return {"text": text_of(payload["content"])}  # userMessage, agentResponse


def to_modifications(method: str, new_value) -> dict:
    """Turn the value the rules changed into ACS modifications."""
    if method == "steps/toolCallRequest":
        return {"parameter_overrides": new_value}  # new tool arguments
    if method == "steps/toolCallResult":
        return {"redactions": [{"path": "/outputs/0/value", "replacement": str(new_value)}]}
    return {"modified_content": new_value}  # new message text


def reply(req: dict, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req.get("id"), "result": result}


def error(req: dict, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": code, "message": message}}


@app.post("/acs")
async def acs(req: dict):
    method = req.get("method")
    params = req.get("params") or {}
    agent = (params.get("metadata") or {}).get("agent_name", "")

    if method == "handshake/hello":
        if ACS_VERSION not in params.get("payload", {}).get("acs_versions_supported", []):
            return error(req, -32001, "UNSUPPORTED_VERSION")
        posture = SERVER_HELLO["on_decision_failure"]
        log_event(logger, "ACS_HANDSHAKE", agent=agent, on_decision_failure=posture)
        return reply(req, SERVER_HELLO)

    if method not in POINTS:
        return error(req, -32601, "Method not found")

    try:
        request_id = params["request_id"]
        snapshot = to_snapshot(method, params["payload"])
    except (KeyError, IndexError, TypeError):
        return error(req, -32602, "Invalid params")

    checked = await engine.evaluate_intervention_point(POINTS[method], snapshot)
    verdict = checked.verdict

    result = {
        "type": "final",
        "acs_version": ACS_VERSION,
        "request_id": request_id,
        "decision": DECISIONS[verdict.decision.value],
    }
    if verdict.reason:
        result["reason_codes"] = [verdict.reason]
        result["policy_references"] = [{"policy_id": "demo_rules", "rule_id": verdict.reason}]
    if result["decision"] != "allow":
        result["reasoning"] = verdict.message or verdict.reason
    if result["decision"] == "modify":
        result["modifications"] = to_modifications(method, checked.transformed_policy_target)
    if result["decision"] == "ask":
        # These agents have no approval screen, so ACS section 9.2 says: deny instead of ask.
        result["decision"] = "deny"
        result["reason_codes"] = result.get("reason_codes", []) + ["approver_unavailable"]

    details = {k: result[k] for k in ("reason_codes", "reasoning", "modifications") if k in result}
    log_event(
        logger,
        "ACS_DECISION",
        agent=agent,
        method=method,
        decision=result["decision"],
        **details,
        payload=params["payload"],
    )
    return reply(req, result)
