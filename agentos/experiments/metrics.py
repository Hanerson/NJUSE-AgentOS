"""Metric definitions for the scheduling report.

All times are virtual milliseconds.  The two token counters are deliberately
returned separately: a second ``LLM_PARSE`` belongs to both sets, so adding
``wasted_llm_tokens`` and ``retry_llm_tokens`` would double count it.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from agentos.kernel.loop import Kernel
    from agentos.kernel.types import RunStats


def p95_ms(samples: Iterable[int]) -> int:
    """Nearest-rank p95. rank = ceil(0.95 * n), one-based."""
    ordered = sorted(samples)
    if not ordered:
        return 0
    rank = math.ceil(0.95 * len(ordered))
    return ordered[rank - 1]


def _class_by_pid(events: Iterable[dict[str, Any]]) -> dict[int, str]:
    return {
        int(row["pid"]): str(row["task_class"])
        for row in events
        if row["event"] == "task_arrive"
    }


def _response_rows(
    events: Iterable[dict[str, Any]],
) -> tuple[dict[int, str], list[tuple[int, str, int, str]]]:
    rows = list(events)
    classes = _class_by_pid(rows)
    arrival = {
        int(row["pid"]): int(row["arrival_ms"])
        for row in rows
        if row["event"] == "task_arrive"
    }
    terminals: list[tuple[int, str, int, str]] = []
    for row in rows:
        if row["event"] != "task_terminal":
            continue
        pid = int(row["pid"])
        if pid not in arrival:
            continue
        terminals.append(
            (pid, classes.get(pid, "unknown"), int(row["terminal_ms"]) - arrival[pid], str(row["state"]))
        )
    return classes, terminals


def _class_metrics(
    classes: dict[int, str],
    terminals: list[tuple[int, str, int, str]],
) -> dict[str, dict[str, float | int]]:
    by_class: dict[str, list[tuple[int, str]]] = {
        task_class: [] for task_class in classes.values()
    }
    for _pid, task_class, response_ms, state in terminals:
        by_class.setdefault(task_class, []).append((response_ms, state))

    result: dict[str, dict[str, float | int]] = {}
    for task_class in sorted(by_class):
        samples = by_class[task_class]
        responses = [value for value, _state in samples]
        done = [value for value, state in samples if state == "DONE"]
        result[task_class] = {
            "n": len(responses),
            "mean_response_ms": (sum(responses) / len(responses)) if responses else 0.0,
            "p95_ms": p95_ms(responses),
            "done_n": len(done),
            "done_mean_ms": (sum(done) / len(done)) if done else 0.0,
        }
    return result


def _llm_steps(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in events
        if row["event"] == "syscall_exit" and row.get("name") == "llm_generate"
    ]


def token_metrics(events: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Return wasted and retry counters without ever combining them."""
    steps = _llm_steps(events)
    wasted = [row for row in steps if row.get("error_class") == "llm_parse"]
    retried = [
        row
        for row in steps
        if int(row.get("consecutive_llm_failures", 0)) > 0
        or int(row.get("consecutive_tool_failures", 0)) > 0
    ]
    return {
        "wasted_llm_tokens": sum(int(row.get("tokens", 0)) for row in wasted),
        "retry_llm_tokens": sum(int(row.get("tokens", 0)) for row in retried),
        "retry_llm_ms": sum(int(row.get("cpu_ms", 0)) for row in retried),
    }


def short_held_selections(
    queue_log: Iterable[dict[str, Any]],
    classes: dict[int, str],
) -> int:
    """``next()`` picked long/flaky while at least one short remained ready."""
    count = 0
    for row in queue_log:
        if row.get("phase") != "after_next":
            continue
        picked = row.get("picked")
        if picked is None or classes.get(int(picked)) not in ("long", "flaky"):
            continue
        if any(classes.get(int(pid)) == "short" for pid in row.get("queue", [])):
            count += 1
    return count


def compute_metrics(
    events: Iterable[dict[str, Any]],
    queue_log: Iterable[dict[str, Any]],
    context_mismatch: int,
) -> dict[str, Any]:
    """One result object for a run.  ``events`` may be ``kernel.trace.events``."""
    rows = list(events)
    classes, terminals = _response_rows(rows)
    return {
        "clock": "virtual",
        "by_class": _class_metrics(classes, terminals),
        **token_metrics(rows),
        "short_held_selections": short_held_selections(queue_log, classes),
        "context_mismatch": context_mismatch,
    }


def metrics_for_run(kernel: Kernel, stats: RunStats) -> dict[str, Any]:
    """Convenience wrapper for ``experiments.run_sched``."""
    return compute_metrics(kernel.trace.events, kernel.queue_log, stats.context_mismatch)
