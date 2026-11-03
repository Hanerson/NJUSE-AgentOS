"""Per-pid snapshots. The step buffer exists only while that pid is inside step."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from agentos.kernel.pcb import KernelError, PCB, ProcTable, SyscallReq
from agentos.kernel.trace import Trace


@dataclass
class Message:
    role: str
    content: str
    step_index: int
    tokens: int


@dataclass
class ContextSnapshot:
    snapshot_id: int
    pid: int
    step_index: int
    messages: list[Message] = field(default_factory=list)
    last_observation: str | None = None
    pending_syscall: SyscallReq | None = None
    context_tokens: int = 0


class ContextManager:
    """Working memory is the snapshot, like a saved trapframe. It is not a page table.

    Session memory is the trace JSONL. Persistent memory is completed.jsonl, read by recall.
    """

    def __init__(self, table: ProcTable, trace: Trace, soft_cap: int) -> None:
        self.table = table
        self.trace = trace
        self.soft_cap = soft_cap
        self.snaps: dict[int, ContextSnapshot] = {}
        self.step_buffers: dict[int, ContextSnapshot] = {}
        self.mismatch = 0

    def create_initial(self, pcb: PCB) -> None:
        snap = ContextSnapshot(
            snapshot_id=1,
            pid=pcb.pid,
            step_index=0,
            messages=[
                Message("system", "Reply with one JSON action.", 0, 0),
                Message("user", pcb.goal, 0, 0),
            ],
            pending_syscall=None,
            context_tokens=0,
        )
        self.snaps[pcb.pid] = snap
        pcb.snapshot_id = snap.snapshot_id
        pcb.context_tokens = 0

    def restore(self, pcb: PCB) -> ContextSnapshot:
        snap = self.snaps.get(pcb.pid)
        if snap is None or snap.pid != pcb.pid:
            self.mismatch += 1
            raise KernelError(f"context mismatch for pid {pcb.pid}")
        buf = deepcopy(snap)
        self.step_buffers[pcb.pid] = buf
        self.trace.context_restore(pcb)
        return buf

    def save(self, pcb: PCB) -> int:
        buf = self.step_buffers[pcb.pid]
        buf.step_index = pcb.step_index
        buf.pending_syscall = deepcopy(pcb.pending_syscall)
        buf.last_observation = pcb.last_observation
        buf.context_tokens = _token_sum(buf.messages)
        new_id = pcb.snapshot_id + 1
        buf.snapshot_id = new_id
        buf.pid = pcb.pid
        stored = deepcopy(buf)
        self.snaps[pcb.pid] = stored
        pcb.snapshot_id = new_id
        pcb.context_tokens = stored.context_tokens
        self.trace.context_save(pcb, new_id, stored.context_tokens, self.soft_cap)
        return new_id

    def append_observation(self, pid: int, message: Message) -> int:
        snap = self.snaps[pid]
        snap.messages.append(deepcopy(message))
        snap.context_tokens = _token_sum(snap.messages)
        new_id = snap.snapshot_id + 1
        snap.snapshot_id = new_id
        pcb = self.table.procs[pid]
        pcb.snapshot_id = new_id
        pcb.context_tokens = snap.context_tokens
        pcb.last_observation = message.content
        return new_id

    def append_to_step_buffer(self, pid: int, message: Message) -> None:
        buf = self.step_buffers[pid]
        buf.messages.append(message)
        buf.context_tokens = _token_sum(buf.messages)

    def step_buffer(self, pid: int) -> ContextSnapshot:
        return self.step_buffers[pid]

    def snapshot_view(self, pid: int) -> ContextSnapshot:
        return self.step_buffers[pid]

    def clear_step_buffer(self) -> None:
        self.step_buffers.clear()


def _token_sum(messages: list[Message]) -> int:
    return sum(message.tokens for message in messages)
