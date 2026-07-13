"""Local token estimation and budget management.

Exact counts require a network call to ``/v1/messages/count_tokens``; for
prompt assembly we only need budgets, so a fast local approximation is used
(~4 characters per token for English/code, tuned conservative). Never used
for billing — only to keep prompts inside configured budgets.
"""

from __future__ import annotations

import json
from typing import Any

CHARS_PER_TOKEN = 3.6


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, int(len(text) / CHARS_PER_TOKEN))


def estimate_message_tokens(message: dict[str, Any]) -> int:
    content = message.get("content", "")
    if isinstance(content, str):
        return estimate_tokens(content) + 4
    total = 8
    for block in content:
        if block.get("type") == "text":
            total += estimate_tokens(block.get("text", ""))
        elif block.get("type") == "tool_use":
            total += estimate_tokens(json.dumps(block.get("input", {}), default=str)) + 12
        elif block.get("type") == "tool_result":
            inner = block.get("content", "")
            if isinstance(inner, str):
                total += estimate_tokens(inner)
            else:
                total += estimate_tokens(json.dumps(inner, default=str))
        elif block.get("type") == "thinking":
            total += estimate_tokens(block.get("thinking", ""))
    return total


def truncate_to_budget(text: str, budget_tokens: int) -> str:
    """Hard-truncate text to roughly ``budget_tokens``, marking the cut."""
    limit = int(budget_tokens * CHARS_PER_TOKEN)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…[truncated]"


def fit_history_to_budget(
    messages: list[dict[str, Any]], budget_tokens: int
) -> tuple[list[dict[str, Any]], bool]:
    """Keep the most recent messages that fit the budget.

    Never splits a message and always keeps at least the final message.
    Ensures the returned window does not start with an orphaned tool_result
    turn (which the API would reject without its matching tool_use).
    Returns (window, dropped_any).
    """
    if not messages:
        return [], False

    kept: list[dict[str, Any]] = []
    total = 0
    for message in reversed(messages):
        cost = estimate_message_tokens(message)
        if kept and total + cost > budget_tokens:
            break
        kept.append(message)
        total += cost
    kept.reverse()

    # Drop a leading user turn made of tool_results — its tool_use was cut.
    while kept and _is_tool_result_turn(kept[0]):
        kept.pop(0)
    # History sent to the API must start with a user turn.
    while kept and kept[0].get("role") != "user":
        kept.pop(0)

    return kept, len(kept) < len(messages)


def _is_tool_result_turn(message: dict[str, Any]) -> bool:
    content = message.get("content")
    return (
        message.get("role") == "user"
        and isinstance(content, list)
        and any(block.get("type") == "tool_result" for block in content)
    )
