"""Acceptance 1: at t=160 the ready queue is [S, L], and L resumes at step 4."""

from __future__ import annotations

from agentos.kernel.pcb import ProcState
from agentos.workloads.demo_preempt import build_preempt


def _by_agent(kernel, pcb_by_agent: dict[str, int], pids: list[int]) -> list[str]:
    inverse = {pid: name for name, pid in pcb_by_agent.items()}
    return [inverse[pid] for pid in pids]


def test_rr_queue_timeline_and_restored_observation(tmp_path) -> None:
    kernel, long_pcb, short_pcb = build_preempt(tmp_path)
    names = {"L": long_pcb.pid, "S": short_pcb.pid}
    stats = kernel.run()

    assert stats.context_mismatch == 0
    assert kernel.context.mismatch == 0
    assert short_pcb.state is ProcState.DONE
    assert short_pcb.exit_reason == "GOAL_DONE"
    assert long_pcb.state is ProcState.DONE
    assert long_pcb.exit_reason == "GOAL_DONE"
    assert long_pcb.step_index >= 4

    flushed = [
        row
        for row in kernel.queue_log
        if row["phase"] == "after_dispatch_flush" and row["now_ms"] == 160
    ]
    assert any(_by_agent(kernel, names, row["queue"]) == ["S", "L"] for row in flushed)

    yielded = [
        row for row in kernel.queue_log if row["phase"] == "after_apply" and row["now_ms"] == 160
    ]
    assert any(row["queue"] == [] and _by_agent(kernel, names, row["yielded"]) == ["L"] for row in yielded)

    picked = [row for row in kernel.queue_log if row["phase"] == "after_next" and row["now_ms"] == 160]
    assert any(
        row["picked"] == short_pcb.pid and _by_agent(kernel, names, row["queue"]) == ["L"] for row in picked
    )

    events = kernel.trace.events
    yield_at = next(
        index
        for index, row in enumerate(events)
        if row["event"] == "sched_yield"
        and row["pid"] == long_pcb.pid
        and row["reason"] == "quantum"
        and row["step_index"] == 3
    )
    terminal_at = next(
        index
        for index, row in enumerate(events)
        if row["event"] == "task_terminal"
        and row["pid"] == short_pcb.pid
        and row["exit_reason"] == "GOAL_DONE"
    )
    restore_at = next(
        index
        for index, row in enumerate(events)
        if index > terminal_at and row["event"] == "context_restore" and row["pid"] == long_pcb.pid
    )
    step4_at = next(
        index
        for index, row in enumerate(events)
        if index > restore_at
        and row["event"] == "syscall_enter"
        and row["pid"] == long_pcb.pid
        and row["name"] == "llm_generate"
        and row["step_index"] == 4
    )
    assert yield_at < terminal_at < restore_at < step4_at

    snap = kernel.context.snaps[long_pcb.pid]
    tool_msgs = [msg for msg in snap.messages if msg.role == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].content == "OBS_STEP2"
    assert tool_msgs[0].step_index == 2
    assert snap.snapshot_id == long_pcb.snapshot_id
    assert all(row["event"] != "priority_decay" for row in events)
