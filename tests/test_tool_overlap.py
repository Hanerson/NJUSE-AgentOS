"""Acceptance 2: while A is blocked on a tool, B occupies the model. FCFS."""

from __future__ import annotations

import time

from agentos.kernel.pcb import ProcState
from agentos.workloads.demo_overlap import build_overlap


def test_tool_wait_overlaps_another_llm(tmp_path) -> None:
    kernel, task_a, task_b = build_overlap(tmp_path)
    started = time.perf_counter()
    stats = kernel.run()
    elapsed = time.perf_counter() - started

    assert elapsed < 1.0
    assert stats.context_mismatch == 0
    assert task_a.state is ProcState.DONE
    assert task_b.state is ProcState.DONE
    assert task_b.exit_reason == "GOAL_DONE"

    events = kernel.trace.events
    block = next(
        row
        for row in events
        if row["event"] == "sched_block" and row["pid"] == task_a.pid and row["state"] == "BLOCKED_TOOL"
    )
    enter = next(
        row
        for row in events
        if row["event"] == "syscall_enter" and row["pid"] == task_b.pid and row["name"] == "llm_generate"
    )
    done = next(row for row in events if row["event"] == "tool_done" and row["pid"] == task_a.pid)
    assert events.index(block) < events.index(enter) < events.index(done)
    assert done["ts_ms"] >= 540
    assert done["at_ms"] == 540
