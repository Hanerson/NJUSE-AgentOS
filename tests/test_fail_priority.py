"""PR7: failure-aware bands, aging, I/O boost, and acceptance 3."""

from __future__ import annotations

from agentos.kernel.config import Config
from agentos.kernel.pcb import PCB, ProcState, transition
from agentos.kernel.sched.base import compute_effective_priority
from agentos.kernel.sched.fail_priority import FailPriorityScheduler
from agentos.kernel.types import ErrorClass, StepKind, StepResult
from agentos.workloads.demo_fail import build_fail, decays, terminal_of
from tests.conftest import make_kernel, spec

PARSE = {"op": "llm", "error_class": "llm_parse", "tokens": 80, "cpu_ms": 40}


def _pcb(pid: int, band: int) -> PCB:
    return PCB(
        pid=pid,
        state=ProcState.READY,
        agent_id=f"agent-{pid}",
        task_class="long",
        base_priority=band,
        effective_priority=band,
        goal="",
        max_steps=1,
        arrival_ms=0,
    )


def _events(kernel, name: str) -> list[dict]:
    return [row for row in kernel.trace.events if row["event"] == name]


def _boost_run(tmp_path, enable_io_boost: bool):
    """A's tool completes while B is in a long model step.

    A wakes in band 3, ahead of B in band 2.  A's next CONTINUE stays in band 3;
    the following step spends the quantum and must fall back to band 2.
    """
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
                {"op": "llm", "tokens": 96, "cpu_ms": 80, "action": {"type": "think"}},
                {"op": "llm", "tokens": 200, "cpu_ms": 40, "action": {"type": "think"}},
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


def test_fail_scheduler_has_five_bands_and_scans_high_first() -> None:
    cfg = Config()
    sched = FailPriorityScheduler(cfg)
    assert sched.uses_bands is True
    assert len(sched.queues) == 5
    high = _pcb(1, 4)
    low = _pcb(2, 2)
    sched.place(low, "tail", None)
    sched.place(high, "tail", None)
    assert sched.next(0) is high


def test_band_change_inserts_at_tail_and_aging_caps() -> None:
    cfg = Config()
    sched = FailPriorityScheduler(cfg)
    high = _pcb(1, 3)
    low = _pcb(2, 2)
    sched.place(high, "tail", None)
    sched.place(low, "tail", None)

    low.wait_ms = 500
    low.effective_priority = compute_effective_priority(low, cfg)
    assert low.effective_priority == 3
    assert sched.relocate_ready(low) is True
    # Aging inserts at the tail of band 3, behind the task already there.
    assert [pcb.pid for pcb in sched.queues[3]] == [1, 2]

    # A penalty/boost band change also forces a tail insert even if head was asked.
    newcomer = _pcb(3, 2)
    sched.place(newcomer, "tail", None)
    newcomer.effective_priority = 3
    sched.place(newcomer, "head", previous_band=2)
    assert [pcb.pid for pcb in sched.queues[3]] == [1, 2, 3]

    low.wait_ms = 10_000
    assert compute_effective_priority(low, cfg) == 4  # base 2 + aging cap 2


def test_ready_wait_ages_to_band_3_and_preempts_at_step_boundary(tmp_path) -> None:
    kernel = make_kernel(tmp_path, policy="fail")
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
        spec(
            agent_id="waiter",
            task_class="short",
            max_steps=4,
            arrival_ms=0,
            plan=[
                {
                    "op": "llm",
                    "tokens": 64,
                    "cpu_ms": 50,
                    "action": {"type": "finish", "summary": "waiter-done"},
                }
            ],
        )
    )
    kernel.run()

    assert runner.state is ProcState.DONE
    assert waiter.state is ProcState.DONE
    boosts = [
        row
        for row in kernel.trace.events
        if row["event"] == "priority_boost" and row["pid"] == waiter.pid
    ]
    assert boosts
    assert (boosts[0]["old"], boosts[0]["new"], boosts[0]["cause"]) == (2, 3, "aging")
    assert boosts[0]["ts_ms"] == 500

    preempt = [
        row
        for row in kernel.trace.events
        if row["event"] == "sched_yield" and row["pid"] == runner.pid
    ]
    assert preempt and preempt[0]["reason"] == "preempt"
    assert preempt[0]["tokens_this_quantum"] == 64  # preserved across preemption

    switch = next(
        row
        for row in kernel.trace.events
        if row["event"] == "sched_switch" and row["next_pid"] == waiter.pid
    )
    assert switch["ts_ms"] == 500
    assert switch["prev_pid"] == runner.pid


def test_io_return_boosts_then_quantum_expiry_clears_it(tmp_path) -> None:
    kernel, task_a, task_b = _boost_run(tmp_path, enable_io_boost=True)
    kernel.run()

    boosts = [
        row
        for row in kernel.trace.events
        if row["event"] == "priority_boost" and row["pid"] == task_a.pid
    ]
    assert len(boosts) == 1
    assert (boosts[0]["old"], boosts[0]["new"], boosts[0]["cause"]) == (2, 3, "io_return")
    assert boosts[0]["ts_ms"] == 2080

    picked_after_wake = next(
        row
        for row in kernel.trace.events
        if row["event"] == "sched_switch"
        and row["next_pid"] == task_a.pid
        and row["ts_ms"] == 2080
    )
    assert picked_after_wake["prev_pid"] == task_b.pid

    # A CONTINUE below the quantum keeps io_boost_pending and stays in band 3.
    early_yields = [
        row
        for row in kernel.trace.events
        if row["event"] == "sched_yield"
        and row["pid"] == task_a.pid
        and row["ts_ms"] < 2200
    ]
    assert early_yields == []

    # 96 + 96 + 200 >= 256 token: the boost is cleared and A re-enters band 2.
    yield_rows = [
        row
        for row in kernel.trace.events
        if row["event"] == "sched_yield" and row["pid"] == task_a.pid
    ]
    assert yield_rows[0]["reason"] == "quantum"
    assert yield_rows[0]["ts_ms"] == 2200
    flushed = next(
        row
        for row in kernel.queue_log
        if row["phase"] == "after_dispatch_flush" and row["now_ms"] == 2200
    )
    assert flushed["queue"] == [task_b.pid, task_a.pid]
    selected = next(
        row
        for row in kernel.queue_log
        if row["phase"] == "after_next" and row["now_ms"] == 2200
    )
    assert selected["picked"] == task_b.pid


def test_no_io_boost_ablation_keeps_the_proc_in_band_2(tmp_path) -> None:
    kernel, task_a, task_b = _boost_run(tmp_path, enable_io_boost=False)
    kernel.run()

    boosts = [
        row
        for row in kernel.trace.events
        if row["event"] == "priority_boost"
        and row["pid"] == task_a.pid
        and row["cause"] == "io_return"
    ]
    assert boosts == []
    assert task_a.effective_priority == 2

    selected = next(
        row
        for row in kernel.queue_log
        if row["phase"] == "after_next" and row["now_ms"] == 2080
    )
    assert selected["picked"] == task_b.pid
    assert task_a.pid in selected["queue"]  # still waiting in band 2


def test_llm_parse_penalty_changes_band_and_tail_inserts(tmp_path) -> None:
    kernel = make_kernel(tmp_path, policy="fail")
    bad = kernel.submit(
        spec(
            agent_id="bad",
            task_class="short",
            max_steps=8,
            arrival_ms=0,
            plan=[
                dict(PARSE),
                dict(PARSE),
                {
                    "op": "llm",
                    "tokens": 64,
                    "cpu_ms": 50,
                    "action": {"type": "finish", "summary": "bad-done"},
                },
            ],
        )
    )
    good = kernel.submit(
        spec(agent_id="good", task_class="short", max_steps=4, arrival_ms=0)
    )
    kernel.run()

    rows = [
        row
        for row in kernel.trace.events
        if row["event"] == "priority_decay" and row["pid"] == bad.pid
    ]
    assert [(row["old"], row["new"], row["cause"]) for row in rows] == [
        (2, 1, "llm_parse"),
        (1, 0, "llm_parse"),
    ]

    after_apply = next(
        row
        for row in kernel.queue_log
        if row["phase"] == "after_apply" and row["now_ms"] == 40
    )
    assert after_apply["queue"] == [good.pid, bad.pid]
    after_next = next(
        row
        for row in kernel.queue_log
        if row["phase"] == "after_next" and row["now_ms"] == 40
    )
    assert after_next["picked"] == good.pid


def test_priority_decay_is_emitted_even_when_the_band_does_not_change(tmp_path) -> None:
    kernel = make_kernel(tmp_path, policy="fail")
    pcb = kernel.submit(
        spec(
            agent_id="floor",
            task_class="short",
            max_steps=4,
            base_priority=0,
            arrival_ms=0,
            plan=[
                dict(PARSE),
                {
                    "op": "llm",
                    "tokens": 64,
                    "cpu_ms": 50,
                    "action": {"type": "finish", "summary": "floor-done"},
                },
            ],
        )
    )
    kernel.run()
    rows = [
        row
        for row in kernel.trace.events
        if row["event"] == "priority_decay" and row["pid"] == pcb.pid
    ]
    assert [(row["old"], row["new"], row["cause"]) for row in rows] == [
        (0, 0, "llm_parse")
    ]


def test_third_llm_parse_is_terminal_without_decay(tmp_path) -> None:
    kernel = make_kernel(tmp_path, policy="fail")
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
    assert bad.queued_band is None

    rows = [
        row
        for row in kernel.trace.events
        if row["event"] == "priority_decay" and row["pid"] == bad.pid
    ]
    assert [(row["old"], row["new"]) for row in rows] == [(2, 1), (1, 0)]
    terminal_index = next(
        index
        for index, row in enumerate(kernel.trace.events)
        if row["event"] == "task_terminal" and row["pid"] == bad.pid
    )
    assert kernel.trace.events.index(rows[-1]) < terminal_index
    assert all(
        row.get("pid") != bad.pid for row in kernel.trace.events[terminal_index + 1 :]
    )


def test_acceptance_3_fail_versus_fcfs(tmp_path) -> None:
    fail_kernel, _, _, fail_flaky = build_fail(tmp_path / "fail", policy="fail")
    fail_kernel.run()
    rows = decays(fail_kernel)
    assert [(row["old"], row["new"], row["cause"]) for row in rows] == [
        (2, 1, "tool_fail"),
        (1, 0, "tool_fail"),
    ]
    terminal = terminal_of(fail_kernel, fail_flaky.pid)
    assert terminal["state"] == "FAILED"
    assert terminal["exit_reason"] == "TOOL_FAIL"
    assert fail_flaky.state is ProcState.FAILED
    assert fail_flaky.queued_band is None
    terminal_index = fail_kernel.trace.events.index(terminal)
    assert fail_kernel.trace.events.index(rows[-1]) < terminal_index
    assert all(
        row.get("pid") != fail_flaky.pid
        for row in fail_kernel.trace.events[terminal_index + 1 :]
    )

    fifo_kernel, _, _, fifo_flaky = build_fail(tmp_path / "fifo", policy="fifo")
    fifo_kernel.run()
    assert decays(fifo_kernel) == []
    assert fifo_flaky.state is ProcState.FAILED
    assert fifo_flaky.exit_reason == "TOOL_FAIL"


def test_llm_resource_blocks_without_penalty(tmp_path) -> None:
    kernel = make_kernel(tmp_path, policy="fail")
    pcb = kernel.submit(
        spec(
            agent_id="R",
            task_class="short",
            max_steps=4,
            arrival_ms=0,
            plan=[
                {"op": "llm", "error_class": "llm_resource", "tokens": 0, "cpu_ms": 10},
                {
                    "op": "llm",
                    "tokens": 64,
                    "cpu_ms": 50,
                    "action": {"type": "finish", "summary": "R-done"},
                },
            ],
        )
    )
    kernel.run()
    assert pcb.state is ProcState.DONE
    assert pcb.consecutive_llm_failures == 0
    assert _events(kernel, "priority_decay") == []
    blocks = [row for row in kernel.trace.events if row["event"] == "sched_block" and row["pid"] == pcb.pid]
    assert blocks and blocks[0]["state"] == "BLOCKED_RESOURCE"
    wakes = [row for row in kernel.trace.events if row["event"] == "sched_wakeup" and row["pid"] == pcb.pid]
    assert wakes and wakes[0]["from_state"] == "BLOCKED_RESOURCE"


def test_resource_block_clears_io_boost_without_decay(tmp_path) -> None:
    kernel = make_kernel(tmp_path, policy="fail")
    pcb = kernel.submit(spec(agent_id="R", task_class="short", max_steps=4))
    kernel.dispatch_due_events()
    assert kernel.sched.next(0) is pcb
    transition(pcb, ProcState.RUNNING)
    pcb.io_boost_pending = True
    pcb.effective_priority = 3
    pcb.tokens_this_quantum = 96

    kernel.apply(
        pcb,
        StepResult(
            kind=StepKind.BLOCK,
            error_class=ErrorClass.LLM_RESOURCE,
            tokens=0,
            cpu_ms=10,
            next_state=ProcState.BLOCKED_RESOURCE,
            observation_summary="llm_resource",
            block_reason="llm_resource",
            block_chan=f"resource:{pcb.pid}",
            not_before_ms=200,
            was_llm=True,
        ),
    )
    assert pcb.state is ProcState.BLOCKED_RESOURCE
    assert pcb.io_boost_pending is False
    assert pcb.effective_priority == 2
    assert pcb.tokens_this_quantum == 96  # blocking preserves the quantum
    assert _events(kernel, "priority_decay") == []


def test_perm_deny_fails_without_penalty(tmp_path) -> None:
    kernel = make_kernel(tmp_path, policy="fail")
    pcb = kernel.submit(
        spec(
            agent_id="D",
            task_class="short",
            max_steps=4,
            arrival_ms=0,
            plan=[
                {
                    "op": "llm",
                    "tokens": 64,
                    "cpu_ms": 40,
                    "action": {"type": "tool", "name": "delete_data", "args": {}},
                },
                {
                    "op": "llm",
                    "tokens": 64,
                    "cpu_ms": 50,
                    "action": {"type": "finish", "summary": "never"},
                },
            ],
        )
    )
    kernel.run()
    assert pcb.state is ProcState.FAILED
    assert pcb.exit_reason == "PERM_DENY"
    assert pcb.consecutive_llm_failures == 0
    assert pcb.consecutive_tool_failures == 0
    assert _events(kernel, "priority_decay") == []


def test_ready_wait_ignores_blocked_and_yielded(tmp_path) -> None:
    kernel = make_kernel(tmp_path, policy="fail")
    blocked = kernel.submit(spec(agent_id="blocked"))
    blocked.state = ProcState.BLOCKED_TOOL
    blocked.queued_band = None
    yielded = kernel.submit(spec(agent_id="yielded"))
    yielded.state = ProcState.READY
    yielded.queued_band = None
    kernel.account_ready_wait(1000)
    assert blocked.wait_ms == 0
    assert yielded.wait_ms == 0
