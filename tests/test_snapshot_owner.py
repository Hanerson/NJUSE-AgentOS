"""A blocked tool completion writes only A's snapshot."""

from __future__ import annotations

from agentos.kernel.pcb import ProcState
from tests.conftest import make_kernel, spec


def test_tool_done_updates_only_the_blocked_pid(tmp_path) -> None:
    kernel = make_kernel(tmp_path)
    task_a = kernel.submit(
        spec(
            agent_id="A",
            task_class="io",
            goal="read something",
            max_steps=8,
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
        spec(
            agent_id="B",
            task_class="short",
            goal="B goal only",
            max_steps=1,
            arrival_ms=0,
            plan=[
                {
                    "op": "llm",
                    "tokens": 64,
                    "cpu_ms": 50,
                    "action": {"type": "think"},
                }
            ],
        )
    )
    stats = kernel.run()

    assert stats.context_mismatch == 0
    assert task_a.state is ProcState.DONE
    snap_a = kernel.context.snaps[task_a.pid]
    snap_b = kernel.context.snaps[task_b.pid]
    assert snap_a.snapshot_id == task_a.snapshot_id
    assert snap_b.snapshot_id == task_b.snapshot_id
    assert snap_a.pid == task_a.pid
    assert snap_b.pid == task_b.pid

    tool_msgs = [msg for msg in snap_a.messages if msg.role == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].content.startswith(f"search-result-{task_a.pid}-")
    assert tool_msgs[0].step_index == 2
    assert all("B goal only" not in msg.content for msg in snap_a.messages)
    assert all(not msg.content.startswith("search-result-") for msg in snap_b.messages)
    assert any("B goal only" in msg.content for msg in snap_b.messages)
