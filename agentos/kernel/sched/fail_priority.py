"""Failure-aware priority bands. Five bands, RR inside a band, penalty only on failures."""

from __future__ import annotations

from agentos.kernel.config import Config
from agentos.kernel.pcb import PCB
from agentos.kernel.sched.base import SchedulerBase


class FailPriorityScheduler(SchedulerBase):
    """Band 4 is the highest. The base class already scans 4 -> 0.

    The policy differs from RR only in two places:
    - ``uses_bands`` is true, so ``place`` reads ``effective_priority`` and a band
      change forces a tail insert;
    - ``preempted_by_higher`` can yield the running proc at a step boundary.

    Failure counters and the K=3 terminal rule live in ``Kernel.apply`` and the
    event handlers, not here.  This class must not inspect model text or goals.
    """

    name = "fail"
    uses_bands = True

    def __init__(self, cfg: Config) -> None:
        self.quantum_tokens = cfg.QUANTUM_TOKENS
        super().__init__()

    def quantum_expired(self, pcb: PCB) -> bool:
        return pcb.tokens_this_quantum >= self.quantum_tokens
