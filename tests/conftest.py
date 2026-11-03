"""Shared builders for kernel tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentos.kernel.config import Config
from agentos.kernel.loop import Kernel, TaskSpec


def make_config(tmp_path: Path) -> Config:
    return Config(
        tool_root=tmp_path / "tool_root",
        persistent_path=tmp_path / "memory" / "completed.jsonl",
        trace_dir=tmp_path / "trace",
    )


def make_kernel(tmp_path: Path, policy: str = "fifo", **cfg_overrides: object) -> Kernel:
    cfg = make_config(tmp_path)
    if cfg_overrides:
        cfg = Config(**{**cfg.__dict__, **cfg_overrides})
    return Kernel(config=cfg, policy=policy)


def spec(**overrides: object) -> TaskSpec:
    data: dict[str, object] = {
        "agent_id": "default",
        "task_class": "short",
        "goal": "finish the task",
        "max_steps": 4,
        "base_priority": 2,
        "deadline_ms": None,
        "arrival_ms": 0,
        "deps": [],
        "plan": [
            {
                "op": "llm",
                "tokens": 64,
                "cpu_ms": 50,
                "action": {"type": "finish", "summary": "ok"},
            }
        ],
        "tool_forces": {},
    }
    data.update(overrides)
    return TaskSpec(**data)  # type: ignore[arg-type]


@pytest.fixture
def kernel(tmp_path: Path) -> Kernel:
    return make_kernel(tmp_path)
