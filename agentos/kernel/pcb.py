"""Process table, legal state edges, and sleep/wakeup channels."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class KernelError(Exception):
    """A kernel invariant failed. Callers are not supposed to catch this and continue."""


class ProcState(Enum):
    READY = "READY"
    RUNNING = "RUNNING"
    BLOCKED_TOOL = "BLOCKED_TOOL"
    BLOCKED_CONFIRM = "BLOCKED_CONFIRM"
    BLOCKED_DEP = "BLOCKED_DEP"
    BLOCKED_RESOURCE = "BLOCKED_RESOURCE"
    DONE = "DONE"
    FAILED = "FAILED"


BLOCKED_STATES = frozenset(
    {
        ProcState.BLOCKED_TOOL,
        ProcState.BLOCKED_CONFIRM,
        ProcState.BLOCKED_DEP,
        ProcState.BLOCKED_RESOURCE,
    }
)

# Edges from the design state diagram. BLOCKED_DEP has no producer on the core path.
_TRANSITIONS = frozenset(
    {
        (ProcState.READY, ProcState.RUNNING),
        (ProcState.RUNNING, ProcState.READY),
        (ProcState.RUNNING, ProcState.BLOCKED_TOOL),
        (ProcState.RUNNING, ProcState.BLOCKED_CONFIRM),
        (ProcState.RUNNING, ProcState.BLOCKED_RESOURCE),
        (ProcState.RUNNING, ProcState.DONE),
        (ProcState.RUNNING, ProcState.FAILED),
        (ProcState.BLOCKED_TOOL, ProcState.BLOCKED_TOOL),
        (ProcState.BLOCKED_TOOL, ProcState.READY),
        (ProcState.BLOCKED_TOOL, ProcState.FAILED),
        (ProcState.BLOCKED_CONFIRM, ProcState.BLOCKED_TOOL),
        (ProcState.BLOCKED_CONFIRM, ProcState.FAILED),
        (ProcState.BLOCKED_RESOURCE, ProcState.READY),
        (ProcState.BLOCKED_DEP, ProcState.READY),
        (ProcState.BLOCKED_DEP, ProcState.FAILED),
    }
)


@dataclass
class SyscallReq:
    name: str
    args: dict


@dataclass
class PCB:
    pid: int
    state: ProcState
    agent_id: str
    task_class: str
    base_priority: int
    effective_priority: int
    goal: str
    max_steps: int
    arrival_ms: int
    consecutive_llm_failures: int = 0
    consecutive_tool_failures: int = 0
    tokens_this_quantum: int = 0
    context_tokens: int = 0
    waiting_reason: str | None = None
    wait_chan: str | None = None
    snapshot_id: int = 0
    service_llm_ms: int = 0
    service_tool_ms: int = 0
    wait_ms: int = 0
    step_index: int = 0
    deadline_ms: int | None = None
    not_before_ms: int | None = None
    io_boost_pending: bool = False
    queued_band: int | None = None
    plan_cursor: int = 0
    pending_syscall: SyscallReq | None = None
    tool_forces: dict[int, str] = field(default_factory=dict)
    deps: list[int] = field(default_factory=list)
    exit_reason: str | None = None
    exit_summary: str | None = None
    last_observation: str | None = None


class ProcTable:
    def __init__(self) -> None:
        self.procs: dict[int, PCB] = {}
        self.wait: dict[str, list[int]] = {}


class _Placer(Protocol):
    def place(self, pcb: PCB, where: str, previous_band: int | None) -> None: ...


def transition(pcb: PCB, new_state: ProcState) -> None:
    if (pcb.state, new_state) not in _TRANSITIONS:
        raise KernelError(f"illegal transition {pcb.state.value} -> {new_state.value}")
    pcb.state = new_state


def wait_channel(kind: str, pcb: PCB) -> str:
    if kind == "confirm":
        return f"confirm:{pcb.pid}"
    if kind == "tool":
        return f"tool:{pcb.pid}:{pcb.step_index}"
    if kind == "resource":
        return f"resource:{pcb.pid}"
    if kind == "dep":
        return f"dep:{pcb.pid}"
    raise KernelError(f"bad channel kind {kind}")


def detach_wait(table: ProcTable, pcb: PCB) -> None:
    chan = pcb.wait_chan
    if chan is None:
        return
    table.wait[chan] = [pid for pid in table.wait.get(chan, []) if pid != pcb.pid]
    if not table.wait.get(chan):
        table.wait.pop(chan, None)
    pcb.wait_chan = None
    pcb.waiting_reason = None


def sleep(table: ProcTable, pcb: PCB, chan: str, state: ProcState, reason: str) -> None:
    if pcb.queued_band is not None:
        raise KernelError(f"pid {pcb.pid} slept while queued")
    detach_wait(table, pcb)
    transition(pcb, state)
    pcb.wait_chan = chan
    pcb.waiting_reason = reason
    table.wait.setdefault(chan, [])
    if pcb.pid not in table.wait[chan]:
        table.wait[chan].append(pcb.pid)


def wakeup(table: ProcTable, sched: _Placer, chan: str, now_ms: int) -> list[PCB]:
    del now_ms  # the caller owns the clock; wakeup itself does not advance it
    pids = table.wait.pop(chan, [])
    woken: list[PCB] = []
    for pid in pids:
        pcb = table.procs[pid]
        if pcb.state not in BLOCKED_STATES or pcb.wait_chan != chan:
            raise KernelError(f"wakeup of non-blocked pid {pid}")
        pcb.wait_chan = None
        pcb.waiting_reason = None
        pcb.not_before_ms = None
        transition(pcb, ProcState.READY)
        pcb.wait_ms = 0
        sched.place(pcb, where="tail", previous_band=None)
        woken.append(pcb)
    return woken
