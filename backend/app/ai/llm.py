"""LLM client abstraction over the Anthropic Messages API.

The brain, planner, and agents depend on the small :class:`LLMClient`
protocol rather than the Anthropic SDK directly, so tests (and any future
provider) can substitute an implementation without touching business logic.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic

from app.core.config import get_settings
from app.core.exceptions import JarvisError

logger = logging.getLogger("jarvis.ai.llm")

# Callback invoked with each streamed text delta.
TextCallback = Callable[[str], Awaitable[None]]


class AIUnavailableError(JarvisError):
    status_code = 503
    code = "ai_unavailable"

    def __init__(self, message: str = "The AI engine is not available right now") -> None:
        super().__init__(message)


class AINotConfiguredError(JarvisError):
    status_code = 503
    code = "ai_not_configured"

    def __init__(
        self,
        message: str = "AI is not configured: set JARVIS_ANTHROPIC_API_KEY on the server",
    ) -> None:
        super().__init__(message)


@dataclass
class LLMResponse:
    """Provider-neutral response: content blocks as plain dicts."""

    content: list[dict[str, Any]]
    stop_reason: str | None
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""

    @property
    def text(self) -> str:
        return "".join(
            block.get("text", "") for block in self.content if block.get("type") == "text"
        )

    @property
    def tool_uses(self) -> list[dict[str, Any]]:
        return [block for block in self.content if block.get("type") == "tool_use"]


@dataclass
class LLMRequest:
    system: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] = field(default_factory=list)
    max_tokens: int = 8192
    effort: str | None = None
    model: str | None = None


class LLMClient(Protocol):
    async def generate(
        self, request: LLMRequest, on_text: TextCallback | None = None
    ) -> LLMResponse: ...

    @property
    def available(self) -> bool: ...


def _blocks_to_dicts(content: list[Any]) -> list[dict[str, Any]]:
    """Convert SDK content blocks to plain dicts safe for JSON storage/replay."""
    blocks: list[dict[str, Any]] = []
    for block in content:
        if block.type == "text":
            blocks.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            blocks.append(
                {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
            )
        elif block.type == "thinking":
            # Preserved for same-model replay; signature must not be altered.
            blocks.append(
                {
                    "type": "thinking",
                    "thinking": block.thinking,
                    "signature": block.signature,
                }
            )
        elif block.type == "redacted_thinking":
            blocks.append({"type": "redacted_thinking", "data": block.data})
    return blocks


class AnthropicLLMClient:
    """Streaming Anthropic client with retry on transient failures."""

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.anthropic_api_key
        self._default_model = settings.ai_model
        self._effort = settings.ai_effort
        self._client: anthropic.AsyncAnthropic | None = (
            anthropic.AsyncAnthropic(api_key=self._api_key) if self._api_key else None
        )

    @property
    def available(self) -> bool:
        return self._client is not None

    async def generate(
        self, request: LLMRequest, on_text: TextCallback | None = None
    ) -> LLMResponse:
        if self._client is None:
            raise AINotConfiguredError()

        model = request.model or self._default_model
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": request.messages,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": request.effort or self._effort},
        }
        if request.tools:
            params["tools"] = request.tools

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                async with self._client.messages.stream(**params) as stream:
                    if on_text is not None:
                        async for text in stream.text_stream:
                            await on_text(text)
                    message = await stream.get_final_message()
                return LLMResponse(
                    content=_blocks_to_dicts(message.content),
                    stop_reason=message.stop_reason,
                    input_tokens=message.usage.input_tokens,
                    output_tokens=message.usage.output_tokens,
                    model=message.model,
                )
            except anthropic.RateLimitError as exc:
                last_error = exc
                retry_after = float(exc.response.headers.get("retry-after", 2 * (attempt + 1)))
                logger.warning("Rate limited by Anthropic; retrying in %.1fs", retry_after)
                await asyncio.sleep(min(retry_after, 30))
            except anthropic.APIStatusError as exc:
                if exc.status_code >= 500:
                    last_error = exc
                    logger.warning("Anthropic server error %s; retrying", exc.status_code)
                    await asyncio.sleep(2 * (attempt + 1))
                else:
                    logger.error("Anthropic request rejected: %s", exc.message)
                    raise AIUnavailableError(f"AI request rejected: {exc.message}") from exc
            except anthropic.APIConnectionError as exc:
                last_error = exc
                logger.warning("Anthropic connection error; retrying")
                await asyncio.sleep(2 * (attempt + 1))

        raise AIUnavailableError() from last_error


_client: AnthropicLLMClient | None = None


def get_llm_client() -> LLMClient:
    """Process-wide LLM client. Tests override via ``set_llm_client``."""
    global _client
    if _client is None:
        _client = AnthropicLLMClient()
    return _client


def set_llm_client(client: LLMClient | None) -> None:
    global _client
    _client = client  # type: ignore[assignment]
