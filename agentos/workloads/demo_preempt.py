"""Acceptance 1. RR, short task arrives at t=160, long task resumes at step 4."""

from __future__ import annotations

from pathlib import Path

from agentos.kernel.config import Config
from agentos.kernel.loop import Kernel
from agentos.kernel.pcb import PCB
from agentos.kernel.types import TaskSpec

LONG_PLAN = [
    {
        "op": "llm",
        "tokens": 80,
        "cpu_ms": 40,
        "action": {"type": "tool", "name": "read_local", "args": {"path": "notes.txt"}},
    },
    {"op": "llm", "tokens": 200, "cpu_ms": 100, "action": {"type": "think"}},
    {"op": "llm", "tokens": 64, "cpu_ms": 40, "action": {"type": "think"}},
]

SHORT_PLAN = [
    {
        "op": "llm",
        "tokens": 64,
        "cpu_ms": 50,
        "action": {"type": "finish", "summary": "short-done"},
    }
]


def build_preempt(root: Path) -> tuple[Kernel, PCB, PCB]:
    tool_root = root / "tool_root"
    tool_root.mkdir(parents=True, exist_ok=True)
    (tool_root / "notes.txt").write_text("OBS_STEP2", encoding="utf-8")
    kernel = Kernel(
        config=Config(
            tool_root=tool_root,
            persistent_path=root / "memory" / "completed.jsonl",
            trace_dir=root / "trace",
        ),
        policy="rr",
    )
    long_pcb = kernel.submit(
        TaskSpec(
            agent_id="L",
            task_class="long",
            goal="read notes and continue",
            max_steps=8,
            base_priority=2,
            arrival_ms=0,
            plan=LONG_PLAN,
        )
    )
    short_pcb = kernel.submit(
        TaskSpec(
            agent_id="S",
            task_class="short",
            goal="finish quickly",
            max_steps=2,
            base_priority=2,
            arrival_ms=160,
            plan=SHORT_PLAN,
        )
    )
    return kernel, long_pcb, short_pcb


def main() -> None:
    kernel, long_pcb, short_pcb = build_preempt(Path("var/demo_preempt"))
    kernel.run()
    print(f"L state={long_pcb.state.value} step={long_pcb.step_index} exit={long_pcb.exit_reason}")
    print(f"S state={short_pcb.state.value} exit={short_pcb.exit_reason}")
    for row in kernel.queue_log:
        if row["now_ms"] == 160 and row["phase"] == "after_dispatch_flush":
            print(f"t=160 queue={row['queue']} yielded={row['yielded']}")


if __name__ == "__main__":
    main()
