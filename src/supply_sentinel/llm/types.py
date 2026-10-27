"""Provider-neutral chat types. Our own small shapes keep cassette keys canonical."""

from typing import Literal, Protocol

from pydantic import BaseModel, Field

Role = Literal["extractor", "specialist", "writer", "judge"]


class ToolCall(BaseModel):
    id: str = ""
    name: str
    args: dict[str, object] = Field(default_factory=dict)
    # Provider fields echoed back verbatim on the next turn (Gemini's thought signature).
    # Kept out of cassette keys so keys stay provider-neutral.
    extra: dict[str, object] | None = None


class Msg(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None  # set on role == "tool"
    name: str | None = None  # tool name on role == "tool"


class LLMResponse(BaseModel):
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    cached: bool = False  # True when served from a cassette


class ProviderError(RuntimeError):
    """The provider failed after all retries (or is unavailable)."""


class ChatClient(Protocol):
    def complete(
        self, role: Role, messages: list[Msg], tools: list[dict[str, object]]
    ) -> LLMResponse: ...

    def model_id(self, role: Role) -> str: ...
