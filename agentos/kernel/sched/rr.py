"""Round-robin on one queue. The slice is tokens, settled only when step returns."""

from __future__ import annotations

from agentos.kernel.config import Config
from agentos.kernel.pcb import PCB
from agentos.kernel.sched.base import SchedulerBase


class RRScheduler(SchedulerBase):
    name = "rr"
    uses_bands = False

    def __init__(self, cfg: Config) -> None:
        self.quantum_tokens = cfg.QUANTUM_TOKENS
        super().__init__()

    def quantum_expired(self, pcb: PCB) -> bool:
        return pcb.tokens_this_quantum >= self.quantum_tokens
