"""Live chat models behind `ChatClient`.

Ollama goes through LangChain `init_chat_model`. OpenAI-compatible endpoints (Gemini) go through
the `openai` SDK directly, because LangChain drops provider fields on tool calls, and Gemini 3
rejects a tool-result turn unless each call's `thought_signature` is echoed back (ADR-0014).
"""

import json
import os
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from openai import OpenAI

from supply_sentinel.llm.config import ProfileConfig
from supply_sentinel.llm.types import LLMResponse, Msg, ProviderError, Role, ToolCall

NUM_CTX = 8192  # Ollama defaults to 4096 and silently truncates; our budget is 7000 input
_OPENAI_CALL_KEYS = {"id", "type", "function", "index"}


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


def _to_openai(messages: list[Msg]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": m.content or None}
            if m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": json.dumps(c.args)},
                        **(c.extra or {}),  # echo provider fields back verbatim
                    }
                    for c in m.tool_calls
                ]
            out.append(msg)
        elif m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id or "", "content": m.content})
        else:
            out.append({"role": m.role, "content": m.content})
    return out


def _from_openai_call(call: Any) -> ToolCall:
    raw = call.to_dict()
    extra = {k: v for k, v in raw.items() if k not in _OPENAI_CALL_KEYS}
    try:
        args = json.loads(call.function.arguments or "{}")
    except json.JSONDecodeError:
        args = {}
    return ToolCall(id=call.id or "", name=call.function.name, args=args, extra=extra or None)


class LiveClient:
    """Builds one chat model (or OpenAI client) per role on first use."""

    def __init__(self, profile: ProfileConfig) -> None:
        if profile.status == "retired":
            raise ProviderError(
                f"Profile '{profile.name}' is retired (see docs/adr/0002). "
                "Use --profile local, or replay."
            )
        self.profile = profile
        self._models: dict[str, Any] = {}
        self._openai: OpenAI | None = None

    def model_id(self, role: Role) -> str:
        return self.profile.role(role).model

    def _max_out(self) -> int:
        return int(self.profile.limits.get("max_output_tokens", 1500))

    def _model(self, role: Role) -> Any:
        if role in self._models:
            return self._models[role]
        p, rc = self.profile, self.profile.role(role)
        if p.provider != "ollama":
            raise ProviderError(
                f"Provider '{p.provider}' is not installed in this project; "
                "add its langchain package to use the premium profile."
            )
        model = init_chat_model(
            rc.model,
            model_provider="ollama",
            temperature=rc.temperature,
            base_url=p.base_url,
            num_ctx=NUM_CTX,
            num_predict=self._max_out(),
            reasoning=False,
            seed=7,
        )
        self._models[role] = model
        return model

    def _openai_client(self) -> OpenAI:
        if self._openai is None:
            p = self.profile
            key = os.environ.get(p.api_key_env or "")
            if not key:
                raise ProviderError(f"Set {p.api_key_env} to use profile '{p.name}'.")
            self._openai = OpenAI(base_url=p.base_url, api_key=key, max_retries=0)
        return self._openai

    def _complete_openai(
        self, role: Role, messages: list[Msg], tools: list[dict[str, object]]
    ) -> LLMResponse:
        rc = self.profile.role(role)
        kwargs: dict[str, Any] = {
            "model": rc.model,
            "messages": _to_openai(messages),
            "temperature": rc.temperature,
            "max_tokens": self._max_out(),
        }
        if tools:
            kwargs["tools"] = [convert_to_openai_tool(t) for t in tools]
        resp = self._openai_client().chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        usage = resp.usage
        return LLMResponse(
            content=msg.content or "",
            tool_calls=[_from_openai_call(c) for c in msg.tool_calls or []],
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            model=rc.model,
        )

    def complete(
        self, role: Role, messages: list[Msg], tools: list[dict[str, object]]
    ) -> LLMResponse:
        if self.profile.provider == "openai":
            return self._complete_openai(role, messages, tools)
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
