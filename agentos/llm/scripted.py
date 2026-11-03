"""Scripted model. The plan cursor moves only inside generate."""

from __future__ import annotations

import json

from agentos.kernel.config import IO_LLM_MS, IO_LLM_TOKENS
from agentos.kernel.context import ContextSnapshot
from agentos.kernel.types import ErrorClass
from agentos.llm.base import Action, LlmResult, parse_action


class ScriptedBackend:
    def __init__(self) -> None:
        self.plans: dict[int, list[dict]] = {}
        self.classes: dict[int, str] = {}
        self.cursors: dict[int, int] = {}

    def register(self, pid: int, task_class: str, plan: list[dict]) -> None:
        self.plans[pid] = list(plan)
        self.classes[pid] = task_class
        self.cursors[pid] = 0

    def generate(self, snap: ContextSnapshot) -> LlmResult:
        pid = snap.pid
        if self.classes.get(pid) == "flaky":
            return self._flaky(snap)
        plan = self.plans.get(pid, [])
        cursor = self.cursors.get(pid, 0)
        if cursor >= len(plan):
            # The design's preemption script ends on its third generate. A later step has nothing left to do.
            payload = {"type": "finish", "summary": "plan-done"}
            action = parse_action(payload)
            action.raw_text = json.dumps(payload, ensure_ascii=False)
            return LlmResult(action.raw_text, 0, 0, ErrorClass.NONE, action)
        item = plan[cursor]
        self.cursors[pid] = cursor + 1
        return _from_item(item)

    def _flaky(self, snap: ContextSnapshot) -> LlmResult:
        last_tool = None
        for message in snap.messages:
            if message.role == "tool":
                last_tool = message
        if last_tool is not None and str(last_tool.content).startswith("search-result-"):
            payload: dict = {"type": "finish", "summary": "flaky-done"}
        else:
            payload = {"type": "tool", "name": "search", "args": {}}
        action = parse_action(payload)
        action.raw_text = json.dumps(payload, ensure_ascii=False)
        return LlmResult(
            text=action.raw_text,
            tokens=IO_LLM_TOKENS,
            cpu_ms=IO_LLM_MS,
            error_class=ErrorClass.NONE,
            action=action,
        )


def _from_item(item: dict) -> LlmResult:
    error_name = item.get("error_class")
    if error_name:
        return LlmResult(
            text="",
            tokens=int(item.get("tokens", 0)),
            cpu_ms=int(item.get("cpu_ms", 0)),
            error_class=ErrorClass(error_name),
            action=None,
        )
    action: Action = parse_action(item["action"])
    raw = json.dumps(item["action"], ensure_ascii=False)
    action.raw_text = raw
    return LlmResult(
        text=raw,
        tokens=int(item["tokens"]),
        cpu_ms=int(item["cpu_ms"]),
        error_class=ErrorClass.NONE,
        action=action,
    )
