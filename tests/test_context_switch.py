"""Role B: per-process context snapshots survive preemption and stay kernel-owned."""

from __future__ import annotations

import pytest

from agentos.kernel.context import ContextSnapshot, Message
from agentos.kernel.pcb import KernelError

from conftest import make_kernel, spec


def test_save_restore_uses_owned_deep_copy_and_preserves_kernel_counters(tmp_path) -> None:
    kernel = make_kernel(tmp_path)
    pcb = kernel.submit(spec())

    buf = kernel.context.restore(pcb)
    buf.messages.append(Message("tool", "OBS_STEP2", 2, 0))
    pcb.step_index = 2
    pcb.consecutive_llm_failures = 1
    pcb.consecutive_tool_failures = 2
    pcb.tokens_this_quantum = 123

    saved_id = kernel.context.save(pcb)
    buf.messages[-1].content = "CALLER_TAMPERED"
    pcb.consecutive_llm_failures = 2

    restored = kernel.context.restore(pcb)
    assert restored.snapshot_id == saved_id == pcb.snapshot_id
    assert [message.content for message in restored.messages if message.role == "tool"] == [
        "OBS_STEP2"
    ]
    assert pcb.consecutive_llm_failures == 2
    assert pcb.consecutive_tool_failures == 2
    assert pcb.tokens_this_quantum == 123
    assert kernel.context.mismatch == 0


def test_append_observation_updates_only_the_target_snapshot(tmp_path) -> None:
    kernel = make_kernel(tmp_path)
    task_a = kernel.submit(spec(agent_id="a"))
    task_b = kernel.submit(spec(agent_id="b"))
    before_b = kernel.context.snaps[task_b.pid]
    before_b_id = before_b.snapshot_id
    before_b_messages = list(before_b.messages)

    new_id = kernel.context.append_observation(
        task_a.pid, Message("tool", "A_ONLY", 2, 0)
    )

    assert kernel.context.snaps[task_a.pid].snapshot_id == new_id == task_a.snapshot_id
    assert kernel.context.snaps[task_a.pid].messages[-1].content == "A_ONLY"
    assert kernel.context.snaps[task_b.pid].snapshot_id == before_b_id
    assert kernel.context.snaps[task_b.pid].messages == before_b_messages
    assert task_b.snapshot_id == before_b_id


def test_restore_rejects_missing_or_foreign_snapshot(tmp_path) -> None:
    kernel = make_kernel(tmp_path)
    pcb = kernel.submit(spec())
    kernel.context.snaps.pop(pcb.pid)

    with pytest.raises(KernelError, match="context mismatch"):
        kernel.context.restore(pcb)
    assert kernel.context.mismatch == 1

    kernel.context.snaps[pcb.pid] = ContextSnapshot(
        snapshot_id=1,
        pid=pcb.pid + 99,
        step_index=0,
    )
    with pytest.raises(KernelError, match="context mismatch"):
        kernel.context.restore(pcb)
    assert kernel.context.mismatch == 2


def test_soft_cap_is_a_warning_and_does_not_drop_messages(tmp_path) -> None:
    kernel = make_kernel(tmp_path, CONTEXT_SOFT_CAP_TOKENS=4)
    pcb = kernel.submit(spec())
    buf = kernel.context.restore(pcb)
    buf.messages.append(Message("assistant", "large-context", 1, 5))
    pcb.step_index = 1

    kernel.context.save(pcb)

    saved = kernel.context.snaps[pcb.pid]
    assert saved.messages[-1].content == "large-context"
    assert saved.context_tokens == 5
    event = kernel.trace.events[-1]
    assert event["event"] == "context_save"
    assert event["warn"] == "context_soft_cap"

