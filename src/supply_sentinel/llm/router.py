"""Router: rate limits, concurrency cap, retries with backoff, and a ledger line per call."""

import random
import threading
import time
from collections.abc import Callable
from typing import Any

from supply_sentinel.llm.replay import CassetteMiss
from supply_sentinel.llm.types import ChatClient, LLMResponse, Msg, ProviderError, Role
from supply_sentinel.observability.ledger import Ledger


class TokenBucket:
    """Allows `rate_per_min` calls per minute; `acquire` sleeps until a token is free."""

    def __init__(
        self,
        rate_per_min: float | None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.rate = rate_per_min
        self._clock, self._sleep = clock, sleep
        self._tokens = float(rate_per_min or 0)
        self._last = clock()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        if not self.rate:
            return
        while True:
            with self._lock:
                now = self._clock()
                self._tokens = min(self.rate, self._tokens + (now - self._last) * self.rate / 60)
                self._last = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait = (1 - self._tokens) * 60 / self.rate
            self._sleep(wait)


class Router:
    def __init__(
        self,
        client: ChatClient,
        limits: dict[str, Any] | None = None,
        ledger: Ledger | None = None,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        limits = limits or {}
        self.client = client
        self.ledger = ledger or Ledger(None)
        self.max_retries = max_retries
        self._sleep = sleep
        self._bucket = TokenBucket(limits.get("rpm"), sleep=sleep)
        self._sem = threading.Semaphore(int(limits.get("concurrency", 2)))

    def model_id(self, role: Role) -> str:
        return self.client.model_id(role)

    def complete(
        self,
        role: Role,
        messages: list[Msg],
        tools: list[dict[str, object]],
        tag: str = "",
        est_input_tokens: int = 0,
    ) -> LLMResponse:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._bucket.acquire()
            try:
                with self._sem:
                    t0 = time.monotonic()
                    resp = self.client.complete(role, messages, tools)
                self.ledger.log(
                    "llm_call",
                    tag=tag,
                    role=role,
                    model=resp.model or self.model_id(role),
                    est_input_tokens=est_input_tokens,
                    input_tokens=resp.input_tokens,
                    output_tokens=resp.output_tokens,
                    tool_calls=[c.name for c in resp.tool_calls],
                    cached=resp.cached,
                    seconds=round(time.monotonic() - t0, 2),
                )
                return resp
            except CassetteMiss:
                raise  # retrying cannot help
            except Exception as exc:  # provider SDKs raise many types; 429s land here too
                last_exc = exc
                self.ledger.log("llm_error", tag=tag, role=role, attempt=attempt, error=str(exc))
                if attempt < self.max_retries:
                    self._sleep(min(30.0, 2.0**attempt) + random.uniform(0, 0.5))
        raise ProviderError(f"LLM call failed after {self.max_retries + 1} attempts: {last_exc}")
