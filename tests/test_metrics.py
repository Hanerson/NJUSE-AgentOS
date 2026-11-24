"""Metric definitions: mean/p95/done-mean, separate token counters, short-held."""

from __future__ import annotations

from agentos.experiments.metrics import (
    compute_metrics,
    metrics_for_run,
    p95_ms,
    short_held_selections,
    token_metrics,
)
from tests.conftest import make_kernel, spec

PARSE = {"op": "llm", "error_class": "llm_parse", "tokens": 80, "cpu_ms": 40}


def test_response_metrics_count_done_and_failed() -> None:
    events = [
        {"event": "task_arrive", "pid": 1, "task_class": "short", "arrival_ms": 0},
        {"event": "task_arrive", "pid": 2, "task_class": "short", "arrival_ms": 100},
        {"event": "task_arrive", "pid": 3, "task_class": "long", "arrival_ms": 0},
        {"event": "task_terminal", "pid": 1, "state": "DONE", "terminal_ms": 50},
        {"event": "task_terminal", "pid": 2, "state": "FAILED", "terminal_ms": 500},
        {"event": "task_terminal", "pid": 3, "state": "DONE", "terminal_ms": 300},
    ]
    metrics = compute_metrics(events, [], context_mismatch=0)

    short = metrics["by_class"]["short"]
    assert short["n"] == 2
    assert short["mean_response_ms"] == 225.0  # (50 + 400) / 2
    assert short["p95_ms"] == 400  # nearest rank, n=2 means the maximum
    assert short["done_n"] == 1
    assert short["done_mean_ms"] == 50.0

    long = metrics["by_class"]["long"]
    assert long["n"] == 1
    assert long["mean_response_ms"] == 300.0
    assert long["p95_ms"] == 300
    assert metrics["context_mismatch"] == 0
    assert metrics["clock"] == "virtual"


def test_p95_uses_ceiling_rank() -> None:
    assert p95_ms([]) == 0
    assert p95_ms(range(1, 17)) == 16  # ceil(0.95 * 16) == 16
    assert p95_ms([5]) == 5


def test_wasted_and_retry_tokens_are_not_added() -> None:
    events = [
        # A successful step before any failure.
        {
            "event": "syscall_exit",
            "name": "llm_generate",
            "pid": 1,
            "tokens": 64,
            "cpu_ms": 50,
            "error_class": "none",
            "consecutive_llm_failures": 0,
            "consecutive_tool_failures": 0,
        },
        # First parse failure: wasted only.
        {
            "event": "syscall_exit",
            "name": "llm_generate",
            "pid": 1,
            "tokens": 80,
            "cpu_ms": 40,
            "error_class": "llm_parse",
            "consecutive_llm_failures": 0,
            "consecutive_tool_failures": 0,
        },
        # Second parse failure: wasted AND retry.  It must not be counted twice
        # by adding the two totals together.
        {
            "event": "syscall_exit",
            "name": "llm_generate",
            "pid": 1,
            "tokens": 80,
            "cpu_ms": 40,
            "error_class": "llm_parse",
            "consecutive_llm_failures": 1,
            "consecutive_tool_failures": 0,
        },
        # Successful recovery step: retry only.
        {
            "event": "syscall_exit",
            "name": "llm_generate",
            "pid": 1,
            "tokens": 64,
            "cpu_ms": 50,
            "error_class": "none",
            "consecutive_llm_failures": 2,
            "consecutive_tool_failures": 0,
        },
        # A tool syscall must not enter the model-token counters.
        {
            "event": "syscall_exit",
            "name": "search",
            "pid": 1,
            "tokens": 0,
            "cpu_ms": 0,
            "error_class": "llm_parse",
            "consecutive_llm_failures": 0,
            "consecutive_tool_failures": 1,
        },
    ]
    metrics = token_metrics(events)
    assert metrics["wasted_llm_tokens"] == 160
    assert metrics["retry_llm_tokens"] == 144
    assert metrics["retry_llm_ms"] == 90
    # The two reported numbers stay separate by construction.
    assert metrics["wasted_llm_tokens"] != (
        metrics["wasted_llm_tokens"] + metrics["retry_llm_tokens"]
    )


def test_short_held_selections() -> None:
    classes = {1: "long", 2: "short", 3: "flaky"}
    queue_log = [
        {"phase": "after_next", "picked": 1, "queue": [2]},
        {"phase": "after_next", "picked": 3, "queue": [2]},
        {"phase": "after_next", "picked": 2, "queue": [1]},
        {"phase": "after_next", "picked": 1, "queue": []},
        {"phase": "after_apply", "picked": 1, "queue": [2]},
    ]
    assert short_held_selections(queue_log, classes) == 2


def test_trace_contains_the_fields_the_metrics_need(tmp_path) -> None:
    kernel = make_kernel(tmp_path, policy="fail")
    kernel.submit(
        spec(
            agent_id="parse-twice",
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
                    "action": {"type": "finish", "summary": "recovered"},
                },
            ],
        )
    )
    stats = kernel.run()
    metrics = metrics_for_run(kernel, stats)
    assert metrics["wasted_llm_tokens"] == 160
    assert metrics["retry_llm_tokens"] == 144
    assert metrics["retry_llm_ms"] == 90
    assert metrics["context_mismatch"] == 0
    assert metrics["by_class"]["short"]["n"] == 1
    assert metrics["by_class"]["short"]["done_n"] == 1
