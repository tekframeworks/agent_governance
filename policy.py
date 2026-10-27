"""Governance rules the Guardian enforces.

The Agent Control Specification engine (see guardian.py) calls Rules.evaluate() for every
step listed in manifest.yaml. check() looks at the step and returns a verdict in the
engine's words: allow, deny, or transform (change the value, then let it through).
The Guardian turns that verdict into an ACS decision: transform becomes modify.

To add a rule, add an `if` to check(). Nothing else needs to change.
"""

import re

SECRET = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}|\bAKIA[0-9A-Z]{16}\b|\bpassword\s*[:=]\s*\S+", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
INJECTION = re.compile(
    r"ignore (?:all )?(?:the )?(?:previous|prior|above) instructions|disregard (?:your|all|the) instructions",
    re.I,
)


def allow():
    return {"decision": "allow"}


def deny(reason, message):
    return {"decision": "deny", "reason": reason, "message": message}


def transform(reason, message, new_value):
    # "$policy_target" means: replace the whole value that manifest.yaml points at
    return {
        "decision": "transform",
        "reason": reason,
        "message": message,
        "transform": {"path": "$policy_target", "value": new_value},
    }


def check(point: str, tool: str | None, value):
    """point: where the agent is (input, pre_tool_call, post_tool_call, output).
    tool: the tool name for the two tool points, else None.
    value: what to check: the message text, the tool arguments, or the tool output."""

    # 1. user message: never send secrets to the model
    if point == "input" and SECRET.search(value):
        return deny("secret_in_prompt", "The message contains an API key or password.")

    # 2. calculator: refuse powers that would freeze the app, e.g. 9**9**9
    if point == "pre_tool_call" and tool == "calculator":
        expression = value.get("expression", "")
        exponents = [int(n) for n in re.findall(r"\*\*\s*(\d+)", expression)]
        if expression.count("**") > 1 or any(n > 100 for n in exponents):
            return deny("expression_too_expensive", "Only one ** with an exponent up to 100 is allowed.")

    # 3. web search: email addresses must not leave the company in a search query
    if point == "pre_tool_call" and tool == "ddg" and EMAIL.search(value.get("query", "")):
        new_args = {**value, "query": EMAIL.sub("[email]", value["query"])}
        return transform("pii_removed", "Email addresses were removed from the search query.", new_args)

    # 4. any tool output: block text that tries to give the agent new instructions
    if point == "post_tool_call" and INJECTION.search(str(value)):
        return deny("prompt_injection", "The tool output tries to give the agent new instructions.")

    # 5. tool output and final answer: mask email addresses
    if point in ("post_tool_call", "output") and EMAIL.search(str(value)):
        return transform("pii_masked", "Email addresses were masked.", EMAIL.sub("[email]", str(value)))

    return allow()


class Rules:
    """The adapter the engine calls (manifest.yaml: policies -> demo_rules, type custom)."""

    def evaluate(self, invocation):
        step = invocation["input"]
        tool = (step.get("tool") or {}).get("name")
        return check(step["intervention_point"], tool, step["policy_target"]["value"])
