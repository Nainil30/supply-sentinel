"""Live chat models behind `ChatClient`, built with LangChain `init_chat_model`."""

import os
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from supply_sentinel.llm.config import ProfileConfig
from supply_sentinel.llm.types import LLMResponse, Msg, ProviderError, Role, ToolCall

NUM_CTX = 8192  # Ollama defaults to 4096 and silently truncates; our budget is 7000 input


def _to_lc(messages: list[Msg]) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    for m in messages:
        if m.role == "system":
            out.append(SystemMessage(content=m.content))
        elif m.role == "user":
            out.append(HumanMessage(content=m.content))
        elif m.role == "assistant":
            calls = [{"id": c.id, "name": c.name, "args": c.args} for c in m.tool_calls]
            out.append(AIMessage(content=m.content, tool_calls=calls))
        else:
            out.append(ToolMessage(content=m.content, tool_call_id=m.tool_call_id or ""))
    return out


class LiveClient:
    """Builds one LangChain chat model per role on first use."""

    def __init__(self, profile: ProfileConfig) -> None:
        if profile.status == "retired":
            raise ProviderError(
                f"Profile '{profile.name}' is retired (see docs/adr/0002). "
                "Use --profile local, or replay."
            )
        self.profile = profile
        self._models: dict[str, Any] = {}

    def model_id(self, role: Role) -> str:
        return self.profile.role(role).model

    def _model(self, role: Role) -> Any:
        if role in self._models:
            return self._models[role]
        p, rc = self.profile, self.profile.role(role)
        max_out = int(p.limits.get("max_output_tokens", 1500))
        kwargs: dict[str, Any] = {"temperature": rc.temperature}
        if p.provider == "ollama":
            kwargs.update(
                base_url=p.base_url, num_ctx=NUM_CTX, num_predict=max_out, reasoning=False, seed=7
            )
        elif p.provider == "openai":
            key = os.environ.get(p.api_key_env or "")
            if not key:
                raise ProviderError(f"Set {p.api_key_env} to use profile '{p.name}'.")
            kwargs.update(base_url=p.base_url, api_key=key, max_tokens=max_out)
        else:
            raise ProviderError(
                f"Provider '{p.provider}' is not installed in this project; "
                "add its langchain package to use the premium profile."
            )
        model = init_chat_model(rc.model, model_provider=p.provider, **kwargs)
        self._models[role] = model
        return model

    def complete(
        self, role: Role, messages: list[Msg], tools: list[dict[str, object]]
    ) -> LLMResponse:
        model = self._model(role)
        if tools:
            model = model.bind_tools(tools)
        ai = model.invoke(_to_lc(messages))
        usage = getattr(ai, "usage_metadata", None) or {}
        content = ai.content if isinstance(ai.content, str) else str(ai.content)
        calls = [
            ToolCall(id=c.get("id") or "", name=c["name"], args=c["args"]) for c in ai.tool_calls
        ]
        return LLMResponse(
            content=content,
            tool_calls=calls,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            model=self.model_id(role),
        )
