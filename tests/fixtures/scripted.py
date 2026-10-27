"""A scripted chat client for tests: no network, no model. It answers from a function."""

from collections.abc import Callable

from supply_sentinel.llm.types import LLMResponse, Msg, Role, ToolCall

Responder = Callable[[Role, list[Msg], list[dict[str, object]]], LLMResponse]


class ScriptedClient:
    def __init__(self, responder: Responder) -> None:
        self.responder = responder
        self.calls: list[tuple[Role, list[Msg], list[dict[str, object]]]] = []

    def model_id(self, role: Role) -> str:
        return "scripted"

    def complete(
        self,
        role: Role,
        messages: list[Msg],
        tools: list[dict[str, object]],
        json_schema: dict[str, object] | None = None,
    ) -> LLMResponse:
        self.calls.append((role, [m.model_copy(deep=True) for m in messages], tools))
        resp = self.responder(role, messages, tools)
        # Token counts as a real provider would report them (chars / 4).
        n_in = sum(len(m.content) for m in messages) // 4
        return resp.model_copy(
            update={"input_tokens": n_in, "output_tokens": len(resp.content) // 4}
        )


def tool_call(name: str, **args: object) -> LLMResponse:
    return LLMResponse(tool_calls=[ToolCall(id="x", name=name, args=args)])


def text(content: str) -> LLMResponse:
    return LLMResponse(content=content)
