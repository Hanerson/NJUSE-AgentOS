"""Action parser shared by the scripted backend and any later HTTP backend."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

from agentos.kernel.context import ContextSnapshot
from agentos.kernel.types import ErrorClass


class ActionParseError(Exception):
    """The model text was not the one JSON action this kernel accepts."""


@dataclass
class Action:
    type: str
    name: str | None = None
    args: dict = field(default_factory=dict)
    summary: str | None = None
    raw_text: str = ""


@dataclass
class LlmResult:
    text: str
    tokens: int
    cpu_ms: int
    error_class: ErrorClass | None
    action: Action | None


class LlmBackend(Protocol):
    def generate(self, snap: ContextSnapshot) -> LlmResult: ...


def parse_action(payload: dict | str) -> Action:
    raw = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    try:
        data = json.loads(raw) if isinstance(payload, str) else payload
    except json.JSONDecodeError as exc:
        raise ActionParseError("invalid json") from exc
    if not isinstance(data, dict) or not isinstance(data.get("type"), str):
        raise ActionParseError("missing type")
    kind = data["type"]
    if kind == "think":
        return Action(type="think", raw_text=raw)
    if kind == "finish":
        summary = data.get("summary")
        if not isinstance(summary, str):
            raise ActionParseError("finish needs summary")
        return Action(type="finish", summary=summary, raw_text=raw)
    if kind == "tool":
        name = data.get("name")
        args = data.get("args", {})
        if not isinstance(name, str) or not isinstance(args, dict):
            raise ActionParseError("tool needs name and args")
        return Action(type="tool", name=name, args=dict(args), raw_text=raw)
    raise ActionParseError(f"unknown action {kind}")
