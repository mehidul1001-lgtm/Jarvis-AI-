"""The AI brain: conversation engine, tool loop, reflection, compression.

Pipeline for one user turn:
  1. Context retrieval — ranked memory search + standing business rules +
     rolling conversation summary.
  2. Prompt assembly — cache-aware system blocks + history fitted to budget.
  3. Reason/act loop — streaming Claude calls; tool_use blocks are executed
     through the permission-checked registry, results returned in a single
     user turn; loop bounded by ``ai_max_tool_iterations``.
  4. Self-validation — optional reflection pass producing a confidence score
     and one bounded revision when the reviewer rejects the draft.
  5. Persistence + telemetry — message stored with blocks, tool trace,
     usage, latency and confidence; events streamed over the user's
     WebSocket; ai.* audit records written.
  6. Context compression — old turns folded into the rolling summary.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm import LLMClient, LLMRequest, LLMResponse, get_llm_client
from app.ai.prompts import REFLECTION_PROMPT, SUMMARIZE_PROMPT, build_system_blocks
from app.ai.tokens import estimate_tokens, fit_history_to_budget
from app.ai.tools.builtin import builtin_registry
from app.ai.tools.registry import ToolContext, ToolRegistry
from app.core.config import get_settings
from app.models.conversation import Conversation, Message, MessageRole
from app.models.memory import MemoryEntry, MemoryKind
from app.models.user import User
from app.services.audit_service import AuditService
from app.services.memory_service import MemoryService

logger = logging.getLogger("jarvis.ai.brain")

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class BrainResult:
    text: str
    blocks: list[dict[str, Any]]
    meta: dict[str, Any] = field(default_factory=dict)


class Brain:
    """Runs reasoning turns. Stateless between calls; safe to construct per request."""

    def __init__(
        self,
        db: AsyncSession,
        user: User,
        *,
        llm: LLMClient | None = None,
        tools: ToolRegistry | None = None,
        persona: str | None = None,
        agent: str | None = None,
    ) -> None:
        self.db = db
        self.user = user
        self.llm = llm or get_llm_client()
        self.tools = tools or builtin_registry
        self.persona = persona
        self.agent = agent
        self.settings = get_settings()
        self.audit = AuditService(db)
        self.memory = MemoryService(db)

    # --- Public entry points ---------------------------------------------------

    async def respond(
        self,
        conversation: Conversation,
        user_text: str,
        *,
        on_event: EventCallback | None = None,
    ) -> BrainResult:
        """Full pipeline for a chat turn (context, tool loop, reflection)."""
        started = time.perf_counter()

        history = await self._load_history(conversation)
        memories, rules = await self._retrieve_context(user_text)
        system = build_system_blocks(
            self.user,
            persona=self.persona,
            business_rules=rules,
            memories=memories,
            conversation_summary=conversation.summary,
        )
        messages = history + [{"role": "user", "content": user_text}]

        result = await self._reason_act_loop(system, messages, on_event=on_event)

        # Self-validation / confidence.
        confidence, issues = await self._reflect(user_text, result)
        if issues and confidence < 0.5 and result.meta.get("revised") is not True:
            result = await self._revise(system, messages, result, issues, on_event=on_event)
            confidence, _ = await self._reflect(user_text, result)
            result.meta["revised"] = True

        result.meta.update(
            {
                "confidence": round(confidence, 3),
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "memories_used": len(memories) + len(rules),
            }
        )
        await self.audit.record(
            "ai.response",
            user_id=self.user.id,
            resource=f"conversation:{conversation.id}",
            detail={
                "agent": self.agent,
                "latency_ms": result.meta["latency_ms"],
                "input_tokens": result.meta.get("input_tokens"),
                "output_tokens": result.meta.get("output_tokens"),
                "tool_calls": len(result.meta.get("tool_trace", [])),
                "confidence": result.meta["confidence"],
            },
        )
        return result

    async def run_objective(
        self,
        objective: str,
        *,
        system_blocks: list[dict[str, Any]] | None = None,
        on_event: EventCallback | None = None,
    ) -> BrainResult:
        """One-shot objective execution (used by agents/planner, no conversation)."""
        system = system_blocks or build_system_blocks(self.user, persona=self.persona)
        messages: list[dict[str, Any]] = [{"role": "user", "content": objective}]
        return await self._reason_act_loop(system, messages, on_event=on_event)

    # --- Context retrieval -------------------------------------------------------

    async def _retrieve_context(
        self, user_text: str
    ) -> tuple[list[MemoryEntry], list[MemoryEntry]]:
        rules_result = await self.db.execute(
            select(MemoryEntry)
            .where(
                MemoryEntry.user_id == self.user.id,
                MemoryEntry.kind == MemoryKind.BUSINESS_RULE,
            )
            .order_by(MemoryEntry.importance.desc())
            .limit(5)
        )
        rules = list(rules_result.scalars().all())
        rule_ids = {rule.id for rule in rules}

        hits = await self.memory.search(self.user, user_text, limit=6)
        memories = [entry for entry, _ in hits if entry.id not in rule_ids]
        return memories, rules

    async def _load_history(self, conversation: Conversation) -> list[dict[str, Any]]:
        result = await self.db.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation.id,
                Message.sequence > conversation.summarized_until,
            )
            .order_by(Message.sequence.asc())
        )
        api_messages: list[dict[str, Any]] = []
        for message in result.scalars().all():
            if message.role == MessageRole.SYSTEM:
                continue
            if message.blocks:
                for block_msg in message.blocks:
                    api_messages.append(block_msg)
            else:
                api_messages.append({"role": message.role.value, "content": message.content})
        window, _ = fit_history_to_budget(api_messages, self.settings.ai_history_token_budget)
        return window

    # --- Reason / act loop ----------------------------------------------------------

    async def _reason_act_loop(
        self,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        *,
        on_event: EventCallback | None = None,
    ) -> BrainResult:
        tool_defs = self.tools.definitions_for(self.user)
        tool_trace: list[dict[str, Any]] = []
        turn_messages: list[dict[str, Any]] = []
        total_in = total_out = 0
        recovered = False

        async def emit_text(text: str) -> None:
            if on_event is not None:
                await on_event({"type": "chat.delta", "text": text})

        response: LLMResponse | None = None
        for _iteration in range(self.settings.ai_max_tool_iterations):
            response = await self.llm.generate(
                LLMRequest(
                    system=system,
                    messages=messages + turn_messages,
                    tools=tool_defs,
                    max_tokens=self.settings.ai_max_output_tokens,
                ),
                on_text=emit_text,
            )
            total_in += response.input_tokens
            total_out += response.output_tokens

            if response.stop_reason == "pause_turn":
                turn_messages.append({"role": "assistant", "content": response.content})
                continue
            if response.stop_reason != "tool_use" or not response.tool_uses:
                break

            turn_messages.append({"role": "assistant", "content": response.content})
            tool_results: list[dict[str, Any]] = []
            ctx = ToolContext(db=self.db, user=self.user, agent=self.agent)
            for tool_use in response.tool_uses:
                if on_event is not None:
                    await on_event(
                        {"type": "chat.tool", "name": tool_use["name"], "status": "running"}
                    )
                outcome = await self.tools.execute(
                    tool_use["name"], tool_use.get("input") or {}, ctx
                )
                tool_trace.append(
                    {
                        "name": tool_use["name"],
                        "input": tool_use.get("input"),
                        "is_error": outcome.is_error,
                        "result_preview": outcome.content[:300],
                    }
                )
                if on_event is not None:
                    await on_event(
                        {
                            "type": "chat.tool",
                            "name": tool_use["name"],
                            "status": "error" if outcome.is_error else "done",
                        }
                    )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use["id"],
                        "content": f"<tool_output>\n{outcome.content}\n</tool_output>",
                        "is_error": outcome.is_error,
                    }
                )
            # All results for the turn go back in ONE user message.
            turn_messages.append({"role": "user", "content": tool_results})
        else:
            # Failure recovery: iteration cap hit — force a final, tool-free answer.
            recovered = True
            turn_messages.append(
                {
                    "role": "user",
                    "content": (
                        "Tool budget exhausted. Summarize what you accomplished, what "
                        "remains, and answer as best you can without more tools."
                    ),
                }
            )
            response = await self.llm.generate(
                LLMRequest(
                    system=system,
                    messages=messages + turn_messages,
                    max_tokens=self.settings.ai_max_output_tokens,
                ),
                on_text=emit_text,
            )
            total_in += response.input_tokens
            total_out += response.output_tokens

        assert response is not None
        blocks = turn_messages + [{"role": "assistant", "content": response.content}]
        return BrainResult(
            text=response.text,
            blocks=blocks,
            meta={
                "tool_trace": tool_trace,
                "input_tokens": total_in,
                "output_tokens": total_out,
                "stop_reason": response.stop_reason,
                "recovered": recovered,
                "model": response.model,
            },
        )

    # --- Reflection / self-validation --------------------------------------------------

    async def _reflect(self, user_text: str, result: BrainResult) -> tuple[float, list[str]]:
        confidence = self._heuristic_confidence(result)
        if not self.settings.ai_reflection_enabled or not result.text.strip():
            return confidence, []
        # Skip the extra model pass for trivial, tool-free small talk.
        if not result.meta.get("tool_trace") and estimate_tokens(result.text) < 120:
            return confidence, []
        try:
            review = await self.llm.generate(
                LLMRequest(
                    system=[{"type": "text", "text": REFLECTION_PROMPT}],
                    messages=[
                        {
                            "role": "user",
                            "content": (
                                f"<request>\n{user_text[:4000]}\n</request>\n"
                                f"<draft>\n{result.text[:8000]}\n</draft>"
                            ),
                        }
                    ],
                    max_tokens=500,
                    effort="low",
                    model=self.settings.ai_utility_model,
                ),
            )
            parsed = _extract_json(review.text)
            model_confidence = float(parsed.get("confidence", confidence))
            issues = [str(issue) for issue in parsed.get("issues", [])]
            if parsed.get("verdict") == "pass":
                issues = []
            return min(confidence, model_confidence) if issues else max(
                confidence, model_confidence
            ), issues
        except Exception:
            logger.warning("Reflection pass failed; using heuristic confidence only")
            return confidence, []

    def _heuristic_confidence(self, result: BrainResult) -> float:
        confidence = 0.9
        trace = result.meta.get("tool_trace", [])
        confidence -= 0.15 * sum(1 for call in trace if call.get("is_error"))
        if result.meta.get("stop_reason") == "max_tokens":
            confidence -= 0.2
        if result.meta.get("recovered"):
            confidence -= 0.3
        if not result.text.strip():
            confidence -= 0.4
        return max(0.05, min(1.0, confidence))

    async def _revise(
        self,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        draft: BrainResult,
        issues: list[str],
        *,
        on_event: EventCallback | None = None,
    ) -> BrainResult:
        if on_event is not None:
            await on_event({"type": "chat.status", "status": "revising"})
        issue_text = "\n".join(f"- {issue}" for issue in issues[:5])
        revision_messages = (
            messages
            + draft.blocks
            + [
                {
                    "role": "user",
                    "content": (
                        "An internal review found problems with your previous answer:\n"
                        f"{issue_text}\nProvide a corrected final answer."
                    ),
                }
            ]
        )
        revised = await self._reason_act_loop(system, revision_messages, on_event=on_event)
        revised.meta["tool_trace"] = draft.meta.get("tool_trace", []) + revised.meta.get(
            "tool_trace", []
        )
        revised.meta["input_tokens"] = draft.meta.get("input_tokens", 0) + revised.meta.get(
            "input_tokens", 0
        )
        revised.meta["output_tokens"] = draft.meta.get("output_tokens", 0) + revised.meta.get(
            "output_tokens", 0
        )
        revised.meta["revised"] = True
        revised.blocks = draft.blocks + revision_messages[-1:] + revised.blocks
        return revised

    # --- Context compression ------------------------------------------------------

    async def compress_if_needed(self, conversation: Conversation) -> bool:
        """Fold older turns into the rolling summary once the threshold is hit."""
        result = await self.db.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation.id,
                Message.sequence > conversation.summarized_until,
            )
            .order_by(Message.sequence.asc())
        )
        pending = list(result.scalars().all())
        threshold = self.settings.ai_compress_after_messages
        if len(pending) <= threshold:
            return False

        keep_tail = max(6, threshold // 3)
        to_fold = pending[:-keep_tail]
        transcript = "\n".join(
            f"{message.role.value}: {message.content[:1500]}" for message in to_fold
        )
        prior = f"Previous summary:\n{conversation.summary}\n\n" if conversation.summary else ""
        try:
            summary = await self.llm.generate(
                LLMRequest(
                    system=[{"type": "text", "text": SUMMARIZE_PROMPT}],
                    messages=[{"role": "user", "content": f"{prior}Conversation:\n{transcript}"}],
                    max_tokens=1200,
                    effort="low",
                    model=self.settings.ai_utility_model,
                ),
            )
        except Exception:
            logger.warning("Conversation compression failed; will retry next turn")
            return False

        conversation.summary = summary.text.strip()[:8000]
        conversation.summarized_until = to_fold[-1].sequence
        await self.db.flush()
        await self.audit.record(
            "ai.context_compressed",
            user_id=self.user.id,
            resource=f"conversation:{conversation.id}",
            detail={"folded_messages": len(to_fold)},
        )
        return True


def _extract_json(text: str) -> dict[str, Any]:
    """Parse the first JSON object found in model output."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in text")
    return json.loads(text[start : end + 1])
