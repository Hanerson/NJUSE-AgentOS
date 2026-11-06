"""Role B: the runtime advances one syscall at a time and classifies termination."""

from __future__ import annotations

import pytest

from agentos.kernel.pcb import ProcState

from conftest import make_kernel, spec


def _error(error_class: str, *, tokens: int = 0, cpu_ms: int = 0) -> dict:
    return {
        "op": "llm",
        "error_class": error_class,
        "tokens": tokens,
        "cpu_ms": cpu_ms,
    }


@pytest.mark.parametrize(
    ("task", "state", "reason"),
    [
        (spec(), ProcState.DONE, "GOAL_DONE"),
        (
            spec(
                max_steps=1,
                plan=[
                    {
                        "op": "llm",
                        "tokens": 64,
                        "cpu_ms": 50,
                        "action": {"type": "think"},
                    }
                ],
            ),
            ProcState.DONE,
            "MAX_STEPS",
        ),
        (spec(deadline_ms=0), ProcState.FAILED, "TIMEOUT"),
        (spec(plan=[_error("llm_http")]), ProcState.FAILED, "LLM_HTTP"),
    ],
)
def test_terminal_classes(tmp_path, task, state: ProcState, reason: str) -> None:
    kernel = make_kernel(tmp_path)
    pcb = kernel.submit(task)

    kernel.run()

    assert pcb.state is state
    assert pcb.exit_reason == reason


def test_three_parse_failures_stop_without_a_fourth_model_call(tmp_path) -> None:
    kernel = make_kernel(tmp_path)
    pcb = kernel.submit(
        spec(plan=[_error("llm_parse", tokens=80, cpu_ms=40) for _ in range(4)])
    )

    kernel.run()

    assert pcb.state is ProcState.FAILED
    assert pcb.exit_reason == "LLM_PARSE"
    assert pcb.consecutive_llm_failures == kernel.config.K == 3
    calls = [
        row
        for row in kernel.trace.events
        if row["event"] == "syscall_enter" and row["name"] == "llm_generate"
    ]
    assert len(calls) == 3


def test_resource_failure_backs_off_without_failure_count(tmp_path) -> None:
    kernel = make_kernel(tmp_path)
    pcb = kernel.submit(
        spec(
            plan=[
                _error("llm_resource", cpu_ms=10),
                {
                    "op": "llm",
                    "tokens": 64,
                    "cpu_ms": 50,
                    "action": {"type": "finish", "summary": "recovered"},
                },
            ]
        )
    )

    kernel.run()

    assert pcb.state is ProcState.DONE
    assert pcb.exit_reason == "GOAL_DONE"
    assert pcb.consecutive_llm_failures == 0
    assert not any(row["event"] == "priority_decay" for row in kernel.trace.events)
    entered = next(
        row
        for row in kernel.trace.events
        if row["event"] == "syscall_enter" and row["name"] == "llm_generate"
    )
    wake = next(
        row
        for row in kernel.trace.events
        if row["event"] == "sched_wakeup" and row["from_state"] == "BLOCKED_RESOURCE"
    )
    # Backoff is registered at step entry; the 10 ms failed-attempt cost is
    # charged afterwards and does not extend the deadline a second time.
    assert wake["ts_ms"] - entered["ts_ms"] == kernel.config.LLM_RESOURCE_BACKOFF_MS

