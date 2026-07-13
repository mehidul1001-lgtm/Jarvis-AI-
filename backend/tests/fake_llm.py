"""Scriptable fake LLM client for tests: no network, deterministic."""

from __future__ import annotations

from typing import Any

from app.ai.llm import LLMRequest, LLMResponse, TextCallback


def text_response(text: str) -> LLMResponse:
    return LLMResponse(
        content=[{"type": "text", "text": text}],
        stop_reason="end_turn",
        input_tokens=50,
        output_tokens=20,
        model="fake-model",
    )


def tool_response(name: str, tool_input: dict[str, Any], text: str = "") -> LLMResponse:
    content: list[dict[str, Any]] = []
    if text:
        content.append({"type": "text", "text": text})
    content.append(
        {
            "type": "tool_use",
            "id": f"toolu_{name}_{id(tool_input)}",
            "name": name,
            "input": tool_input,
        }
    )
    return LLMResponse(
        content=content,
        stop_reason="tool_use",
        input_tokens=60,
        output_tokens=30,
        model="fake-model",
    )


class FakeLLMClient:
    """Returns queued responses in order; records every request it receives."""

    def __init__(self, responses: list[LLMResponse] | None = None) -> None:
        self.responses = list(responses or [])
        self.requests: list[LLMRequest] = []
        self.default = text_response("OK.")

    def queue(self, *responses: LLMResponse) -> None:
        self.responses.extend(responses)

    @property
    def available(self) -> bool:
        return True

    async def generate(
        self, request: LLMRequest, on_text: TextCallback | None = None
    ) -> LLMResponse:
        self.requests.append(request)
        response = self.responses.pop(0) if self.responses else self.default
        if on_text is not None and response.stop_reason != "tool_use":
            for chunk in _chunks(response.text, 12):
                await on_text(chunk)
        return response


def _chunks(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]
