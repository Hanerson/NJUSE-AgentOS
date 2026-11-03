"""Shared kernel records that more than one subsystem has to name."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from agentos.kernel.pcb import ProcState


class StepKind(Enum):
    CONTINUE = "continue"
    DONE = "done"
    BLOCK = "block"
    FAIL = "fail"


class ErrorClass(Enum):
    NONE = "none"
    LLM_RESOURCE = "llm_resource"
    LLM_HTTP = "llm_http"
    LLM_PARSE = "llm_parse"
    TOOL_FAIL = "tool_fail"
    PERM_DENY = "perm_deny"
    DEP_FAIL = "dep_fail"
    TIMEOUT = "timeout"
    MAX_STEPS = "max_steps"
    GOAL_DONE = "goal_done"


@dataclass
class StepResult:
    kind: StepKind
    error_class: ErrorClass
    tokens: int
    cpu_ms: int
    next_state: ProcState
    observation_summary: str
    block_reason: str | None = None
    block_chan: str | None = None
    not_before_ms: int | None = None
    was_llm: bool = False


@dataclass
class TaskSpec:
    agent_id: str
    task_class: str
    goal: str
    max_steps: int
    base_priority: int = 2
    deadline_ms: int | None = None
    arrival_ms: int = 0
    deps: list[int] = field(default_factory=list)
    plan: list[dict] = field(default_factory=list)
    tool_forces: dict[int, str] = field(default_factory=dict)


@dataclass
class ClassStats:
    n: int = 0
    mean_response_ms: float = 0.0


@dataclass
class RunStats:
    context_mismatch: int
    by_class: dict[str, ClassStats]
    trace_path: str
    blocked_dep_pids: list[int]
