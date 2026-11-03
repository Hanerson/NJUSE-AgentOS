"""PR5: FCFS keeps the head across step boundaries. No quantum rotation."""

from __future__ import annotations

from agentos.kernel.pcb import ProcState, transition
from agentos.kernel.sched.fifo import FifoScheduler
from agentos.kernel.types import ErrorClass, StepKind, StepResult
from tests.conftest import make_kernel, spec


def _events(kernel, name: str) -> list[dict]:
    return [row for row in kernel.trace.events if row["event"] == name]


def test_fifo_next_removes_the_head_until_it_is_placed_back(tmp_path) -> None:
    kernel = make_kernel(tmp_path)
    assert isinstance(kernel.sched, FifoScheduler)
    first = kernel.submit(spec(agent_id="long", task_class="long", arrival_ms=0))
    second = kernel.submit(spec(agent_id="short", task_class="short", arrival_ms=0))
    kernel.dispatch_due_events()
    assert kernel.sched.queue_pids() == [first.pid, second.pid]

    picked = kernel.sched.next(kernel.clock.now_ms)
    assert picked is first
    assert first.queued_band is None
    assert first.pid not in kernel.sched.queue_pids()
    assert kernel.sched.queue_pids() == [second.pid]

    # FCFS has one queue. A priority band number must not force a tail insert.
    kernel.sched.place(first, where="head", previous_band=first.effective_priority)
    assert kernel.sched.queue_pids() == [first.pid, second.pid]
    kernel.sched.next(kernel.clock.now_ms)
    kernel.sched.place(first, where="head", previous_band=None)
    again = kernel.sched.next(kernel.clock.now_ms)
    assert again is first
    assert first.pid not in kernel.sched.queue_pids()


def test_observation_summary_does_not_change_fcfs_order(tmp_path) -> None:
    def once(summary: str) -> tuple[int, int | None]:
        kernel = make_kernel(tmp_path / summary)
        pcb = kernel.submit(spec(agent_id="only", plan=[]))
        kernel.dispatch_due_events()
        picked = kernel.sched.next(0)
        assert picked is pcb
        transition(picked, ProcState.RUNNING)
        kernel.apply(
            pcb,
            StepResult(
                kind=StepKind.CONTINUE,
                error_class=ErrorClass.NONE,
                tokens=64,
                cpu_ms=50,
                next_state=ProcState.READY,
                observation_summary=summary,
                was_llm=True,
            ),
        )
        assert pcb.pid in kernel.sched.queue_pids()
        return pcb.effective_priority, kernel.sched.queue_pids()[0]

    priority_a, pid_a = once("alpha")
    priority_b, pid_b = once("completely different summary")
    assert priority_a == priority_b
    assert pid_a == pid_b


def test_long_task_convoy_holds_a_later_short_task(tmp_path) -> None:
    kernel = make_kernel(tmp_path)
    long_plan = [
        {"op": "llm", "tokens": 160, "cpu_ms": 120, "action": {"type": "think"}},
        {"op": "llm", "tokens": 160, "cpu_ms": 120, "action": {"type": "think"}},
        {
            "op": "llm",
            "tokens": 160,
            "cpu_ms": 120,
            "action": {"type": "finish", "summary": "long-done"},
        },
    ]
    long_pcb = kernel.submit(
        spec(
            agent_id="long",
            task_class="long",
            goal="long goal",
            max_steps=16,
            arrival_ms=0,
            plan=long_plan,
        )
    )
    short_pcb = kernel.submit(
        spec(
            agent_id="short",
            task_class="short",
            goal="short goal",
            max_steps=4,
            arrival_ms=30,
            plan=[
                {
                    "op": "llm",
                    "tokens": 64,
                    "cpu_ms": 50,
                    "action": {"type": "finish", "summary": "short-done"},
                }
            ],
        )
    )
    kernel.run()

    assert long_pcb.state is ProcState.DONE
    assert long_pcb.exit_reason == "GOAL_DONE"
    assert short_pcb.state is ProcState.DONE

    terminal = next(
        row for row in kernel.trace.events if row["event"] == "task_terminal" and row["pid"] == long_pcb.pid
    )
    short_enter = next(
        row
        for row in kernel.trace.events
        if row["event"] == "syscall_enter"
        and row["pid"] == short_pcb.pid
        and row["name"] == "llm_generate"
    )
    assert kernel.trace.events.index(terminal) < kernel.trace.events.index(short_enter)
    assert _events(kernel, "sched_yield") == []
    assert _events(kernel, "priority_decay") == []

    long_picks = [
        row
        for row in kernel.queue_log
        if row["phase"] == "after_next" and row["picked"] == long_pcb.pid
    ]
    assert len(long_picks) >= 3
    for row in long_picks:
        assert long_pcb.pid not in row["queue"]


def test_parse_failures_stay_at_the_head_then_fail_without_decay(tmp_path) -> None:
    kernel = make_kernel(tmp_path)
    parse = {"op": "llm", "error_class": "llm_parse", "tokens": 80, "cpu_ms": 40}
    bad = kernel.submit(
        spec(
            agent_id="bad",
            task_class="short",
            goal="bad",
            max_steps=8,
            arrival_ms=0,
            plan=[parse, parse, parse],
        )
    )
    short = kernel.submit(
        spec(
            agent_id="short",
            task_class="short",
            goal="short",
            max_steps=4,
            arrival_ms=0,
        )
    )
    kernel.run()

    assert bad.state is ProcState.FAILED
    assert bad.exit_reason == "LLM_PARSE"
    assert bad.consecutive_llm_failures == 3
    assert bad.queued_band is None
    assert short.state is ProcState.DONE
    assert _events(kernel, "priority_decay") == []

    bad_enters = [
        row
        for row in kernel.trace.events
        if row["event"] == "syscall_enter" and row["pid"] == bad.pid and row["name"] == "llm_generate"
    ]
    assert len(bad_enters) == 3
    short_enter = next(
        row
        for row in kernel.trace.events
        if row["event"] == "syscall_enter" and row["pid"] == short.pid and row["name"] == "llm_generate"
    )
    assert kernel.trace.events.index(bad_enters[-1]) < kernel.trace.events.index(short_enter)
    bad_terminal = next(
        row for row in kernel.trace.events if row["event"] == "task_terminal" and row["pid"] == bad.pid
    )
    assert kernel.trace.events.index(bad_terminal) < kernel.trace.events.index(short_enter)


def test_third_tool_failure_is_terminal_without_decay(tmp_path) -> None:
    kernel = make_kernel(tmp_path)
    bad = kernel.submit(
        spec(
            agent_id="bad",
            task_class="flaky",
            goal="search until the kernel stops it",
            max_steps=12,
            arrival_ms=0,
            plan=[],
            tool_forces={2: "fail", 4: "fail", 6: "fail"},
        )
    )
    kernel.run()
    assert bad.state is ProcState.FAILED
    assert bad.exit_reason == "TOOL_FAIL"
    assert bad.consecutive_tool_failures == 3
    assert bad.queued_band is None
    assert _events(kernel, "priority_decay") == []
    assert sum(1 for row in kernel.trace.events if row["event"] == "task_terminal") == 1
