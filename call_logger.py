from langchain_core.callbacks import BaseCallbackHandler

from call_log import log_event


class CallLogger(BaseCallbackHandler):
    """Logs every LLM call and tool call that create_agent makes.

    Works for one agent or many: when an agent has a name (create_agent(name=...)),
    that name is added to each log line as "agent". LangChain only passes the agent
    name on "start" events, so we remember it per run_id and reuse it on the matching
    "end" / "error" event (important when agents run in parallel).
    """

    run_inline = True  # keep log lines in call order

    def __init__(self, logger):
        self.logger = logger
        self._agent_by_run = {}

    def _log(self, event, kwargs, *, start=False, **data):
        run_id = kwargs.get("run_id")
        if start:
            self._agent_by_run[run_id] = (kwargs.get("metadata") or {}).get("lc_agent_name")
            agent = self._agent_by_run[run_id]
        else:
            agent = self._agent_by_run.pop(run_id, None)
        if agent:
            data = {"agent": agent, **data}
        log_event(self.logger, event, **data)

    def on_chat_model_start(self, serialized, messages, **kwargs):
        self._log(
            "LLM_CALL",
            kwargs,
            start=True,
            messages=[{"type": m.type, "content": m.content} for m in messages[0]],
        )

    def on_llm_end(self, response, **kwargs):
        msg = response.generations[0][0].message
        self._log("LLM_RESULT", kwargs, content=msg.content, tool_calls=msg.tool_calls)

    def on_llm_error(self, error, **kwargs):
        self._log("LLM_ERROR", kwargs, error=repr(error))

    def on_tool_start(self, serialized, input_str, **kwargs):
        self._log("TOOL_CALL", kwargs, start=True, tool=serialized.get("name"), input=input_str)

    def on_tool_end(self, output, **kwargs):
        self._log(
            "TOOL_RESULT",
            kwargs,
            tool=getattr(output, "name", None),
            output=getattr(output, "content", output),
        )

    def on_tool_error(self, error, **kwargs):
        self._log("TOOL_ERROR", kwargs, error=repr(error))
