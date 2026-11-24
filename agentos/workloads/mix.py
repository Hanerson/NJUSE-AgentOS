"""Deterministic mixed workload: 16 short, 12 long, 8 io, 4 flaky at N=40.

Three random streams are used by one experiment and they never share state:
``rng_shape`` and ``rng_order`` live here, ``rng_search`` lives on the Kernel.
This module must not create a fourth generator or draw a search outcome.
"""

from __future__ import annotations

import random
from copy import deepcopy

from agentos.kernel.config import (
    ARRIVAL_GAP_MS,
    DEFAULT_BASE_PRIORITY,
    EXPERIMENT_N,
    EXPERIMENT_SEED,
    IO_LLM_MS,
    IO_LLM_TOKENS,
    LONG_LLM_MS,
    LONG_LLM_TOKENS,
    MAX_STEPS_BY_CLASS,
    SHORT_LLM_MS,
    SHORT_LLM_TOKENS,
)
from agentos.kernel.types import TaskSpec

CLASS_ORDER = ("short", "long", "io", "flaky")
FIXED_COUNTS = {"short": 16, "long": 12, "io": 8, "flaky": 4}

IO_PLAN: list[dict] = [
    {
        "op": "llm",
        "tokens": IO_LLM_TOKENS,
        "cpu_ms": IO_LLM_MS,
        "action": {"type": "tool", "name": "read_local", "args": {"path": "notes.txt"}},
    },
    {
        "op": "llm",
        "tokens": IO_LLM_TOKENS,
        "cpu_ms": IO_LLM_MS,
        "action": {"type": "tool", "name": "read_local", "args": {"path": "notes.txt"}},
    },
    {
        "op": "llm",
        "tokens": IO_LLM_TOKENS,
        "cpu_ms": IO_LLM_MS,
        "action": {"type": "tool", "name": "read_local", "args": {"path": "notes.txt"}},
    },
    {
        "op": "llm",
        "tokens": IO_LLM_TOKENS,
        "cpu_ms": IO_LLM_MS,
        "action": {"type": "finish", "summary": "io-done"},
    },
]


def class_counts(n: int = EXPERIMENT_N) -> dict[str, int]:
    """N=40 is the frozen composition. Other sizes keep the same proportions."""
    if n <= 0:
        raise ValueError("n must be positive")
    if n == EXPERIMENT_N:
        return dict(FIXED_COUNTS)
    base = [
        n * FIXED_COUNTS[cls] // EXPERIMENT_N for cls in CLASS_ORDER
    ]
    remainder = n - sum(base)
    for index in range(remainder):
        base[index % len(base)] += 1
    return dict(zip(CLASS_ORDER, base))


def _short_plan(think_steps: int) -> list[dict]:
    plan = [
        {
            "op": "llm",
            "tokens": SHORT_LLM_TOKENS,
            "cpu_ms": SHORT_LLM_MS,
            "action": {"type": "think"},
        }
        for _ in range(think_steps)
    ]
    plan.append(
        {
            "op": "llm",
            "tokens": SHORT_LLM_TOKENS,
            "cpu_ms": SHORT_LLM_MS,
            "action": {"type": "finish", "summary": "short-done"},
        }
    )
    return plan


def _long_plan(think_steps: int) -> list[dict]:
    plan = [
        {
            "op": "llm",
            "tokens": LONG_LLM_TOKENS,
            "cpu_ms": LONG_LLM_MS,
            "action": {"type": "think"},
        }
        for _ in range(think_steps)
    ]
    plan.append(
        {
            "op": "llm",
            "tokens": LONG_LLM_TOKENS,
            "cpu_ms": LONG_LLM_MS,
            "action": {"type": "finish", "summary": "long-done"},
        }
    )
    return plan


def _io_plan() -> list[dict]:
    return deepcopy(IO_PLAN)


def build_mix(seed: int = EXPERIMENT_SEED, n: int = EXPERIMENT_N) -> list[TaskSpec]:
    """Build the workload in the documented draw order.

    1. ``rng_shape``: all 16 short draws, then all 12 long draws.
    2. The unshuffled list is short*, long*, io*, flaky*.
    3. ``rng_order`` shuffles that list exactly once.
    4. Task i arrives at ``i * ARRIVAL_GAP_MS``.
    """
    counts = class_counts(n)
    rng_shape = random.Random(seed)
    rng_order = random.Random(seed)

    shaped: list[tuple[str, list[dict]]] = []
    for task_class in CLASS_ORDER:
        for _ in range(counts[task_class]):
            if task_class == "short":
                plan = _short_plan(1 + rng_shape.randrange(2))
            elif task_class == "long":
                plan = _long_plan(8 + rng_shape.randrange(5))
            elif task_class == "io":
                plan = _io_plan()
            else:
                # FlakySearchPlan is not pre-expanded; the scripted backend
                # follows the last tool observation at generate time.
                plan = []
            shaped.append((task_class, plan))

    rng_order.shuffle(shaped)

    return [
        TaskSpec(
            agent_id=f"{task_class}-{index}",
            task_class=task_class,
            goal=f"{task_class} workload item",
            max_steps=MAX_STEPS_BY_CLASS[task_class],
            base_priority=DEFAULT_BASE_PRIORITY,
            deadline_ms=None,
            arrival_ms=index * ARRIVAL_GAP_MS,
            deps=[],
            plan=plan,
            tool_forces={},
        )
        for index, (task_class, plan) in enumerate(shaped)
    ]
