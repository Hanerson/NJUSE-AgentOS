"""PR7 ablation switches: penalty and I/O boost can be disabled independently."""

from __future__ import annotations

from agentos.kernel.pcb import ProcState
from agentos.kernel.sched.fail_priority import FailPriorityScheduler
from agentos.kernel.types import TaskSpec
from agentos.workloads.demo_fail import FAILING_TOOL_STEPS, SHORT_PLAN, build_fail
from tests.conftest import make_kernel, spec

PARSE = {"op": "llm", "error_class": "llm_parse", "tokens": 80, "cpu_ms": 40}


def _events(kernel, name: str) -> list[dict]:
    return [row for row in kernel.trace.events if row["event"] == name]


def test_no_fail_penalty_keeps_failure_counts_and_k(tmp_path) -> None:
    kernel = make_kernel(tmp_path, policy="fail", enable_fail_penalty=False)
    bad = kernel.submit(
        spec(
            agent_id="bad",
            task_class="short",
            max_steps=8,
            arrival_ms=0,
            plan=[dict(PARSE), dict(PARSE), dict(PARSE)],
        )
    )
    kernel.run()

    assert bad.state is ProcState.FAILED
    assert bad.exit_reason == "LLM_PARSE"
    assert bad.consecutive_llm_failures == 3
    rows = [
        row
        for row in kernel.trace.events
        if row["event"] == "priority_decay" and row["pid"] == bad.pid
    ]
    # The penalty path still reports; the A/B terms are zero, so old == new.
    assert [(row["old"], row["new"], row["cause"]) for row in rows] == [
        (2, 2, "llm_parse"),
        (2, 2, "llm_parse"),
    ]


def test_no_fail_penalty_does_not_drop_the_band(tmp_path) -> None:
    kernel = make_kernel(tmp_path, policy="fail", enable_fail_penalty=False)
    pcb = kernel.submit(
        spec(
            agent_id="two-fails",
            task_class="short",
            max_steps=2,
            arrival_ms=0,
            plan=[dict(PARSE), dict(PARSE)],
        )
    )
    kernel.run()

    assert pcb.exit_reason == "MAX_STEPS"
    assert pcb.consecutive_llm_failures == 2
    assert pcb.effective_priority == 2
    assert pcb.queued_band is None


def test_no_fail_penalty_still_applies_tool_failure_k(tmp_path) -> None:
    # build_fail uses the default Config; rebuild with the ablation off.
    kernel = make_kernel(tmp_path / "off", policy="fail", enable_fail_penalty=False)

    kernel.submit(
        TaskSpec(
            agent_id="short-a",
            task_class="short",
            goal="finish",
            max_steps=4,
            base_priority=2,
            arrival_ms=0,
            plan=SHORT_PLAN,
        )
    )
    kernel.submit(
        TaskSpec(
            agent_id="short-b",
            task_class="short",
            goal="finish",
            max_steps=4,
            base_priority=2,
            arrival_ms=0,
            plan=SHORT_PLAN,
        )
    )
    flaky = kernel.submit(
        TaskSpec(
            agent_id="flaky",
            task_class="flaky",
            goal="search",
            max_steps=12,
            base_priority=2,
            arrival_ms=0,
            plan=[],
            tool_forces=dict(FAILING_TOOL_STEPS),
        )
    )
    kernel.run()

    assert flaky.state is ProcState.FAILED
    assert flaky.exit_reason == "TOOL_FAIL"
    assert flaky.consecutive_tool_failures == 3
    rows = [
        row
        for row in kernel.trace.events
        if row["event"] == "priority_decay" and row["pid"] == flaky.pid
    ]
    assert [(row["old"], row["new"], row["cause"]) for row in rows] == [
        (2, 2, "tool_fail"),
        (2, 2, "tool_fail"),
    ]


def _boost_scenario(tmp_path, enable_io_boost: bool):
    kernel = make_kernel(tmp_path, policy="fail", enable_io_boost=enable_io_boost)
    task_a = kernel.submit(
        spec(
            agent_id="A",
            task_class="io",
            max_steps=8,
            arrival_ms=0,
            tool_forces={2: "ok"},
            plan=[
                {
                    "op": "llm",
                    "tokens": 96,
                    "cpu_ms": 80,
                    "action": {"type": "tool", "name": "search", "args": {}},
                },
                {
                    "op": "llm",
                    "tokens": 64,
                    "cpu_ms": 50,
                    "action": {"type": "finish", "summary": "A-done"},
                },
            ],
        )
    )
    task_b = kernel.submit(
        spec(
            agent_id="B",
            task_class="long",
            max_steps=4,
            arrival_ms=0,
            plan=[
                {"op": "llm", "tokens": 200, "cpu_ms": 2000, "action": {"type": "think"}},
                {
                    "op": "llm",
                    "tokens": 64,
                    "cpu_ms": 50,
                    "action": {"type": "finish", "summary": "B-done"},
                },
            ],
        )
    )
    return kernel, task_a, task_b


def test_no_io_boost_removes_the_return_boost(tmp_path) -> None:
    kernel, task_a, task_b = _boost_scenario(tmp_path, enable_io_boost=False)
    kernel.run()
    boosts = [
        row
        for row in kernel.trace.events
        if row["event"] == "priority_boost"
        and row["pid"] == task_a.pid
        and row["cause"] == "io_return"
    ]
    assert boosts == []
    selected = next(
        row
        for row in kernel.queue_log
        if row["phase"] == "after_next" and row["now_ms"] == 2080
    )
    assert selected["picked"] == task_b.pid
    assert task_a.pid in selected["queue"]


def test_both_switches_off_is_not_rr(tmp_path) -> None:
    kernel = make_kernel(
        tmp_path,
        policy="fail",
        enable_fail_penalty=False,
        enable_io_boost=False,
    )
    assert isinstance(kernel.sched, FailPriorityScheduler)
    assert kernel.sched.uses_bands is True
    assert len(kernel.sched.queues) == 5

    runner = kernel.submit(
        spec(
            agent_id="runner",
            task_class="long",
            max_steps=4,
            arrival_ms=0,
            plan=[
                {"op": "llm", "tokens": 64, "cpu_ms": 500, "action": {"type": "think"}},
                {
                    "op": "llm",
                    "tokens": 64,
                    "cpu_ms": 50,
                    "action": {"type": "finish", "summary": "runner-done"},
                },
            ],
        )
    )
    waiter = kernel.submit(
        spec(agent_id="waiter", task_class="short", max_steps=4, arrival_ms=0)
    )
    kernel.run()

    boosts = [
        row
        for row in kernel.trace.events
        if row["event"] == "priority_boost"
        and row["pid"] == waiter.pid
        and row["cause"] == "aging"
    ]
    assert boosts
    assert (boosts[0]["old"], boosts[0]["new"]) == (2, 3)
    switches = [
        row
        for row in kernel.trace.events
        if row["event"] == "sched_switch" and row["next_pid"] == waiter.pid
    ]
    assert switches and switches[0]["ts_ms"] == 500
    assert runner.state is ProcState.DONE


def test_fcfs_and_rr_have_no_priority_decay_but_still_stop_at_k(tmp_path) -> None:
    for policy in ("fifo", "rr"):
        kernel, _, _, flaky = build_fail(tmp_path / policy, policy=policy)
        kernel.run()
        assert _events(kernel, "priority_decay") == []
        assert flaky.state is ProcState.FAILED
        assert flaky.exit_reason == "TOOL_FAIL"
        assert flaky.consecutive_tool_failures == 3
