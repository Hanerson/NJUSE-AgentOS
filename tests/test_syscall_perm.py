"""Role B: syscall validation, permissions, confirmation, tools, and recall."""

from __future__ import annotations

import json

import pytest

from agentos.kernel.pcb import KernelError, ProcState

from conftest import make_kernel, spec


def _tool_plan(name: str, args: dict, summary: str = "done") -> list[dict]:
    return [
        {
            "op": "llm",
            "tokens": 64,
            "cpu_ms": 50,
            "action": {"type": "tool", "name": name, "args": args},
        },
        {
            "op": "llm",
            "tokens": 64,
            "cpu_ms": 50,
            "action": {"type": "finish", "summary": summary},
        },
    ]


def test_read_local_is_event_driven_and_does_not_advance_clock_when_issued(tmp_path) -> None:
    kernel = make_kernel(tmp_path)
    note = kernel.config.tool_root / "notes.txt"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("OBS_FROM_FILE", encoding="utf-8")
    pcb = kernel.submit(spec(plan=_tool_plan("read_local", {"path": "notes.txt"})))

    kernel.dispatch_due_events()
    picked = kernel.sched.next(kernel.clock.now_ms)
    assert picked is pcb
    # Run the model decision, then issue exactly one pending syscall by hand.
    from agentos.kernel.pcb import transition

    transition(pcb, ProcState.RUNNING)
    kernel.context.restore(pcb)
    first = kernel.runtime.step(pcb)
    kernel.context.save(pcb)
    kernel.context.clear_step_buffer()
    kernel.clock.advance(first.cpu_ms)
    kernel.apply(pcb, first)

    picked = kernel.sched.next(kernel.clock.now_ms)
    assert picked is pcb
    transition(pcb, ProcState.RUNNING)
    kernel.context.restore(pcb)
    before_issue = kernel.clock.now_ms
    issued = kernel.runtime.step(pcb)

    assert issued.next_state is ProcState.BLOCKED_TOOL
    assert issued.cpu_ms == 0
    assert kernel.clock.now_ms == before_issue
    event = kernel.events.peek()
    assert event is not None
    assert event.kind == "tool_done"
    assert event.at_ms == before_issue + kernel.config.READ_LOCAL_SERVICE_MS


@pytest.mark.parametrize("tool", ["delete_data", "unknown_tool"])
def test_deny_and_unknown_tool_fail_without_side_effects(tmp_path, tool: str) -> None:
    kernel = make_kernel(tmp_path)
    pcb = kernel.submit(spec(plan=_tool_plan(tool, {})))

    kernel.run()

    assert pcb.state is ProcState.FAILED
    assert pcb.exit_reason == "PERM_DENY"
    assert pcb.consecutive_llm_failures == 0
    assert pcb.consecutive_tool_failures == 0
    assert pcb.pending_syscall is None
    assert pcb.wait_chan is None
    assert not kernel.table.wait
    assert not any(row["event"] == "tool_done" for row in kernel.trace.events)
    assert not any(row["event"] == "priority_decay" for row in kernel.trace.events)


def test_read_local_path_escape_is_permission_denied(tmp_path) -> None:
    kernel = make_kernel(tmp_path)
    pcb = kernel.submit(spec(plan=_tool_plan("read_local", {"path": "../secret.txt"})))

    kernel.run()

    assert pcb.state is ProcState.FAILED
    assert pcb.exit_reason == "PERM_DENY"
    assert not any(row["event"] == "tool_done" for row in kernel.trace.events)


def test_invalid_tool_arguments_are_parse_errors_not_tool_failures(tmp_path) -> None:
    kernel = make_kernel(tmp_path)
    pcb = kernel.submit(
        spec(plan=_tool_plan("write_report", {"content": "x" * 4097}))
    )

    kernel.run()

    assert pcb.state is ProcState.DONE
    assert pcb.consecutive_tool_failures == 0
    invalid_exit = next(
        row
        for row in kernel.trace.events
        if row["event"] == "syscall_exit" and row["name"] == "write_report"
    )
    assert invalid_exit["error_class"] == "llm_parse"
    assert not any(row["event"] == "tool_done" for row in kernel.trace.events)
    assert not (kernel.config.tool_root / "reports" / f"{pcb.pid}.txt").exists()


def test_write_report_waits_for_confirmation_then_changes_channels(tmp_path) -> None:
    kernel = make_kernel(tmp_path)
    pcb = kernel.submit(
        spec(plan=_tool_plan("write_report", {"content": "approved report"}))
    )
    report = kernel.config.tool_root / "reports" / f"{pcb.pid}.txt"

    kernel.run()

    assert pcb.state is ProcState.BLOCKED_CONFIRM
    assert pcb.pending_syscall is not None
    assert pcb.pending_syscall.name == "write_report"
    assert kernel.context.snaps[pcb.pid].pending_syscall is not None
    assert pcb.wait_chan == f"confirm:{pcb.pid}"
    assert not report.exists()

    now = kernel.clock.now_ms
    kernel.confirm(pcb.pid, True)
    assert pcb.state is ProcState.BLOCKED_TOOL
    assert pcb.pending_syscall is None
    assert pcb.wait_chan == f"tool:{pcb.pid}:{pcb.step_index}"
    assert f"confirm:{pcb.pid}" not in kernel.table.wait
    event = kernel.events.peek()
    assert event is not None and event.at_ms == now + kernel.config.WRITE_REPORT_SERVICE_MS
    assert not report.exists()

    kernel.run()

    assert pcb.state is ProcState.DONE
    assert pcb.exit_reason == "GOAL_DONE"
    assert report.read_text(encoding="utf-8") == "approved report"


def test_confirmation_rejection_cleans_wait_state_without_failure_penalty(tmp_path) -> None:
    kernel = make_kernel(tmp_path)
    pcb = kernel.submit(spec(plan=_tool_plan("write_report", {"content": "no"})))
    kernel.run()

    kernel.confirm(pcb.pid, False)

    assert pcb.state is ProcState.FAILED
    assert pcb.exit_reason == "PERM_DENY"
    assert pcb.pending_syscall is None
    assert pcb.wait_chan is None
    assert pcb.consecutive_llm_failures == 0
    assert pcb.consecutive_tool_failures == 0
    assert not kernel.table.wait
    assert not any(row["event"] == "priority_decay" for row in kernel.trace.events)
    with pytest.raises(KernelError, match="confirm of non-waiting proc"):
        kernel.confirm(pcb.pid, True)


def test_tool_force_is_looked_up_by_the_issuing_step(tmp_path) -> None:
    kernel = make_kernel(tmp_path)
    pcb = kernel.submit(
        spec(
            plan=_tool_plan("search", {}),
            tool_forces={2: "fail"},
        )
    )

    kernel.run()

    tool_done = next(row for row in kernel.trace.events if row["event"] == "tool_done")
    assert tool_done["issued_step"] == 2
    assert tool_done["ok"] is False
    assert pcb.consecutive_tool_failures == 1
    assert pcb.state is ProcState.DONE


def test_recall_returns_only_the_latest_twenty_matching_records(tmp_path) -> None:
    kernel = make_kernel(tmp_path)
    rows = [
        {
            "pid": index,
            "agent_id": "a",
            "task_class": "short",
            "summary": f"os-{index}",
            "finished_ms": index,
        }
        for index in range(25)
    ]
    kernel.config.persistent_path.parent.mkdir(parents=True, exist_ok=True)
    kernel.config.persistent_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    pcb = kernel.submit(spec(plan=_tool_plan("recall", {"query": "os-"})))

    kernel.run()

    tool_messages = [
        message for message in kernel.context.snaps[pcb.pid].messages if message.role == "tool"
    ]
    recalled = json.loads(tool_messages[-1].content)
    assert len(recalled) == 20
    assert recalled[0]["pid"] == 5
    assert recalled[-1]["pid"] == 24
    assert pcb.service_tool_ms == 0
    assert pcb.consecutive_tool_failures == 0
    recall_exit = next(
        row
        for row in kernel.trace.events
        if row["event"] == "syscall_exit" and row["name"] == "recall"
    )
    assert recall_exit["error_class"] == "none"


def test_completed_memory_records_only_goal_done(tmp_path) -> None:
    kernel = make_kernel(tmp_path)
    done = kernel.submit(spec(agent_id="done"))
    denied = kernel.submit(
        spec(agent_id="denied", plan=_tool_plan("delete_data", {}), arrival_ms=10)
    )

    kernel.run()

    lines = kernel.config.persistent_path.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    assert done.state is ProcState.DONE
    assert denied.state is ProcState.FAILED
    assert [record["pid"] for record in records] == [done.pid]

