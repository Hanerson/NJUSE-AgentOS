"""PR8 workload: fixed composition, documented RNG order, replayable trace."""

from __future__ import annotations

import random
from collections import Counter

from agentos.kernel.config import EXPERIMENT_SEED, Config
from agentos.kernel.loop import Kernel
from agentos.workloads.mix import IO_PLAN, build_mix


def test_mix_has_fixed_composition_and_arrival_grid() -> None:
    specs = build_mix(seed=EXPERIMENT_SEED)
    assert len(specs) == 40
    assert Counter(task.task_class for task in specs) == {
        "short": 16,
        "long": 12,
        "io": 8,
        "flaky": 4,
    }
    assert [task.arrival_ms for task in specs] == [index * 30 for index in range(40)]
    assert all(task.base_priority == 2 for task in specs)
    assert all(task.deps == [] for task in specs)


def test_mix_matches_the_documented_draw_order() -> None:
    specs = build_mix(seed=EXPERIMENT_SEED)
    rng_shape = random.Random(EXPERIMENT_SEED)
    expected: list[tuple[str, int | None]] = []
    for _ in range(16):
        expected.append(("short", 1 + rng_shape.randrange(2)))
    for _ in range(12):
        expected.append(("long", 8 + rng_shape.randrange(5)))
    for _ in range(8):
        expected.append(("io", None))
    for _ in range(4):
        expected.append(("flaky", None))
    random.Random(EXPERIMENT_SEED).shuffle(expected)

    for spec, (task_class, think_steps) in zip(specs, expected):
        assert spec.task_class == task_class
        if task_class == "short":
            assert len(spec.plan) == think_steps + 1
            assert spec.plan[-1]["action"] == {"type": "finish", "summary": "short-done"}
            assert all(item["tokens"] == 64 and item["cpu_ms"] == 50 for item in spec.plan)
        elif task_class == "long":
            assert len(spec.plan) == think_steps + 1
            assert spec.plan[-1]["action"] == {"type": "finish", "summary": "long-done"}
            assert all(item["tokens"] == 160 and item["cpu_ms"] == 120 for item in spec.plan)
        elif task_class == "io":
            assert spec.plan == IO_PLAN
        else:
            assert spec.plan == []
            assert spec.tool_forces == {}
            assert spec.max_steps == 12


def test_mix_uses_the_class_max_steps() -> None:
    specs = build_mix(seed=EXPERIMENT_SEED)
    expected = {"short": 4, "long": 16, "io": 16, "flaky": 12}
    for task in specs:
        assert task.max_steps == expected[task.task_class]


def _run_once(root, specs) -> list[dict]:
    tool_root = root / "tool_root"
    tool_root.mkdir(parents=True, exist_ok=True)
    (tool_root / "notes.txt").write_text("notes", encoding="utf-8")
    kernel = Kernel(
        config=Config(
            tool_root=tool_root,
            persistent_path=root / "memory" / "completed.jsonl",
            trace_dir=root / "trace",
        ),
        policy="fail",
    )
    for task in specs:
        kernel.submit(task)
    kernel.run()
    return kernel.trace.events


def test_same_seed_replays_the_same_trace(tmp_path) -> None:
    specs = build_mix(seed=EXPERIMENT_SEED)
    first = _run_once(tmp_path / "first", specs)
    second = _run_once(tmp_path / "second", specs)
    assert first == second
