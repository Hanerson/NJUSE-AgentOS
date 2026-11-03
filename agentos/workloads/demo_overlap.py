"""Acceptance 2. FCFS: B's model step sits between A's tool block and A's tool_done."""

from __future__ import annotations

from pathlib import Path

from agentos.kernel.config import Config
from agentos.kernel.loop import Kernel
from agentos.kernel.pcb import PCB
from agentos.kernel.types import TaskSpec


def build_overlap(root: Path) -> tuple[Kernel, PCB, PCB]:
    kernel = Kernel(
        config=Config(
            tool_root=root / "tool_root",
            persistent_path=root / "memory" / "completed.jsonl",
            trace_dir=root / "trace",
        ),
        policy="fifo",
    )
    task_a = kernel.submit(
        TaskSpec(
            agent_id="A",
            task_class="io",
            goal="search then finish",
            max_steps=8,
            base_priority=2,
            arrival_ms=0,
            tool_forces={2: "ok"},
            plan=[
                {
                    "op": "llm",
                    "tokens": 64,
                    "cpu_ms": 40,
                    "action": {"type": "tool", "name": "search", "args": {}},
                },
                {
                    "op": "llm",
                    "tokens": 64,
                    "cpu_ms": 40,
                    "action": {"type": "finish", "summary": "A-done"},
                },
            ],
        )
    )
    task_b = kernel.submit(
        TaskSpec(
            agent_id="B",
            task_class="short",
            goal="B finishes while A waits",
            max_steps=2,
            base_priority=2,
            arrival_ms=0,
            plan=[
                {
                    "op": "llm",
                    "tokens": 64,
                    "cpu_ms": 50,
                    "action": {"type": "finish", "summary": "B-done"},
                }
            ],
        )
    )
    return kernel, task_a, task_b


def main() -> None:
    kernel, task_a, task_b = build_overlap(Path("var/demo_overlap"))
    kernel.run()
    done = next(row for row in kernel.trace.events if row["event"] == "tool_done" and row["pid"] == task_a.pid)
    print(f"A={task_a.state.value} B={task_b.state.value} tool_done.ts_ms={done['ts_ms']}")


if __name__ == "__main__":
    main()
