"""In-memory trace plus a JSONL file. Event names are the closed set from the design."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from agentos.kernel.clock import VirtualClock
from agentos.kernel.pcb import PCB


class Trace:
    def __init__(self, directory: Path, clock: VirtualClock, run_id: str | None = None) -> None:
        self.clock = clock
        self.run_id = run_id or uuid4().hex
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{self.run_id}.jsonl"
        self.events: list[dict[str, Any]] = []

    def emit(self, event: str, **fields: Any) -> None:
        row: dict[str, Any] = {"ts_ms": self.clock.now_ms, "event": event}
        for key, value in fields.items():
            if value is not None or key in ("prev_pid",):
                row[key] = value
        self.events.append(row)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def task_arrive(self, pcb: PCB) -> None:
        self.emit("task_arrive", pid=pcb.pid, task_class=pcb.task_class, arrival_ms=pcb.arrival_ms)

    def sched_switch(self, prev: PCB | None, pcb: PCB, reason: str) -> None:
        self.emit(
            "sched_switch",
            prev_pid=None if prev is None else prev.pid,
            next_pid=pcb.pid,
            reason=reason,
        )

    def sched_yield(self, pcb: PCB, reason: str, tokens_this_quantum: int) -> None:
        self.emit(
            "sched_yield",
            pid=pcb.pid,
            reason=reason,
            tokens_this_quantum=tokens_this_quantum,
            step_index=pcb.step_index,
        )

    def sched_block(self, pcb: PCB) -> None:
        self.emit(
            "sched_block",
            pid=pcb.pid,
            chan=pcb.wait_chan,
            state=pcb.state.value,
            waiting_reason=pcb.waiting_reason,
        )

    def sched_wakeup(self, pcb: PCB, chan: str, from_state: str) -> None:
        self.emit("sched_wakeup", pid=pcb.pid, chan=chan, from_state=from_state)

    def sched_idle(self, now_ms: int, jump_to_ms: int) -> None:
        self.emit("sched_idle", now_ms=now_ms, jump_to_ms=jump_to_ms)

    def priority_decay(self, pcb: PCB, old: int, new: int, cause: str) -> None:
        self.emit(
            "priority_decay",
            pid=pcb.pid,
            old=old,
            new=new,
            cause=cause,
            consecutive_llm_failures=pcb.consecutive_llm_failures,
            consecutive_tool_failures=pcb.consecutive_tool_failures,
        )

    def priority_boost(self, pcb: PCB, old: int, new: int, cause: str) -> None:
        self.emit("priority_boost", pid=pcb.pid, old=old, new=new, cause=cause)

    def context_save(self, pcb: PCB, snapshot_id: int, context_tokens: int, soft_cap: int) -> None:
        fields: dict[str, Any] = {
            "pid": pcb.pid,
            "snapshot_id": snapshot_id,
            "step_index": pcb.step_index,
        }
        if context_tokens > soft_cap:
            fields["warn"] = "context_soft_cap"
        self.emit("context_save", **fields)

    def context_restore(self, pcb: PCB) -> None:
        self.emit(
            "context_restore",
            pid=pcb.pid,
            snapshot_id=pcb.snapshot_id,
            step_index=pcb.step_index,
        )

    def syscall_enter(self, pcb: PCB, name: str) -> None:
        self.emit("syscall_enter", pid=pcb.pid, name=name, step_index=pcb.step_index)

    def syscall_exit(self, pcb: PCB, name: str, error_class: str, next_state: str, summary: str) -> None:
        self.emit(
            "syscall_exit",
            pid=pcb.pid,
            name=name,
            error_class=error_class,
            next_state=next_state,
            observation_summary=summary[:80],
        )

    def perm_check(self, pcb: PCB, tool: str, decision: str) -> None:
        self.emit("perm_check", pid=pcb.pid, agent_id=pcb.agent_id, tool=tool, decision=decision)

    def task_terminal(self, pcb: PCB) -> None:
        self.emit(
            "task_terminal",
            pid=pcb.pid,
            state=pcb.state.value,
            exit_reason=pcb.exit_reason,
            terminal_ms=self.clock.now_ms,
        )

    def tool_done(self, pcb: PCB, at_ms: int, ok: bool, issued_step: int) -> None:
        self.emit(
            "tool_done",
            pid=pcb.pid,
            at_ms=at_ms,
            ok=ok,
            issued_step=issued_step,
        )
