"""Issuing a tool records a future event and does not move the virtual clock."""

from __future__ import annotations

from agentos.kernel.pcb import SyscallReq
from tests.conftest import make_kernel, spec


def test_issue_tool_leaves_clock_and_schedules_service(tmp_path) -> None:
    kernel = make_kernel(tmp_path)
    notes = kernel.config.tool_root / "notes.txt"
    notes.parent.mkdir(parents=True, exist_ok=True)
    notes.write_text("OBS_STEP2", encoding="utf-8")

    pcb = kernel.submit(
        spec(
            task_class="io",
            max_steps=8,
            tool_forces={2: "ok"},
            plan=[
                {
                    "op": "llm",
                    "tokens": 64,
                    "cpu_ms": 40,
                    "action": {
                        "type": "tool",
                        "name": "read_local",
                        "args": {"path": "notes.txt"},
                    },
                }
            ],
        )
    )
    pcb.step_index = 2
    before = kernel.clock.now_ms
    result = kernel.syscall.issue_tool(pcb, SyscallReq("read_local", {"path": "notes.txt"}))
    assert kernel.clock.now_ms == before
    assert result.cpu_ms == 0
    tool_events = [event for event in kernel.events.pending() if event.kind == "tool_done"]
    assert len(tool_events) == 1
    event = tool_events[0]
    assert event.at_ms == before + 20
    assert event.pid == pcb.pid
    assert pcb.state.name == "READY"
    assert pcb.wait_chan is None
