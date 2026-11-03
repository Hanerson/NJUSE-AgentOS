"""Scheduler protocol. One queue when uses_bands is false. Bands force a tail insert."""

from __future__ import annotations

from collections import deque

from agentos.kernel.config import Config
from agentos.kernel.pcb import KernelError, PCB, ProcState


def compute_effective_priority(pcb: PCB, cfg: Config) -> int:
    """Priority formula shared by every policy. FCFS and RR do not read the result to pick."""
    aging = min(pcb.wait_ms // cfg.AGING_QUANTUM_MS, cfg.AGING_CAP)
    raw = pcb.base_priority
    if cfg.enable_fail_penalty:
        raw -= cfg.A * pcb.consecutive_llm_failures
        raw -= cfg.B * pcb.consecutive_tool_failures
    if cfg.enable_io_boost and pcb.io_boost_pending:
        raw += cfg.C
    raw += aging
    return max(cfg.PRIO_MIN, min(cfg.PRIO_MAX, raw))


class SchedulerBase:
    name = "base"
    uses_bands = False

    def __init__(self) -> None:
        width = 5 if self.uses_bands else 1
        self.queues: list[deque[PCB]] = [deque() for _ in range(width)]

    def place(self, pcb: PCB, where: str, previous_band: int | None) -> None:
        if where not in ("head", "tail"):
            raise KernelError(f"bad place {where}")
        new_band = pcb.effective_priority if self.uses_bands else 0
        # A single queue has no band to change. Forcing tail here would turn FCFS into 1-step RR.
        if self.uses_bands and previous_band is not None and previous_band != new_band:
            where = "tail"
        if pcb.queued_band is not None:
            self.dequeue(pcb)
        self._enqueue(pcb, new_band, where)
        pcb.queued_band = new_band

    def dequeue(self, pcb: PCB) -> None:
        band = pcb.queued_band
        if band is None:
            return
        queue = self.queues[band]
        kept = deque(item for item in queue if item is not pcb)
        if len(kept) == len(queue):
            raise KernelError(f"pid {pcb.pid} not in ready queue")
        self.queues[band] = kept
        pcb.queued_band = None

    def next(self, now_ms: int) -> PCB | None:
        for band in range(len(self.queues) - 1, -1, -1):
            queue = self.queues[band]
            if not queue:
                continue
            pcb = queue[0]
            if pcb.state is not ProcState.READY:
                raise KernelError(f"ready queue held non-READY pid {pcb.pid}")
            if pcb.not_before_ms is not None and now_ms < pcb.not_before_ms:
                raise KernelError(f"ready queue held not-yet-due pid {pcb.pid}")
            queue.popleft()
            pcb.queued_band = None
            return pcb
        return None

    def quantum_expired(self, pcb: PCB) -> bool:
        del pcb
        return False

    def preempted_by_higher(self, pcb: PCB) -> bool:
        if not self.uses_bands:
            return False
        current = pcb.effective_priority
        for band in range(current + 1, len(self.queues)):
            if self.queues[band]:
                return True
        return False

    def relocate_ready(self, pcb: PCB) -> bool:
        """Move a READY proc when aging changed its band. Same band keeps its place."""
        if not self.uses_bands or pcb.queued_band is None:
            return False
        new_band = pcb.effective_priority
        if new_band == pcb.queued_band:
            return False
        old = pcb.queued_band
        self.dequeue(pcb)
        self._enqueue(pcb, new_band, "tail")
        pcb.queued_band = new_band
        return new_band > old

    def queue_pids(self) -> list[int]:
        pids: list[int] = []
        for band in range(len(self.queues) - 1, -1, -1):
            pids.extend(pcb.pid for pcb in self.queues[band])
        return pids

    def _enqueue(self, pcb: PCB, band: int, where: str) -> None:
        if band < 0 or band >= len(self.queues):
            raise KernelError(f"band {band} out of range")
        queue = self.queues[band]
        if where == "head":
            queue.appendleft(pcb)
        else:
            queue.append(pcb)
