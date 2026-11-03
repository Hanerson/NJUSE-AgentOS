"""FCFS. A step boundary puts the same proc back at the head. The quantum is ignored."""

from __future__ import annotations

from agentos.kernel.pcb import PCB
from agentos.kernel.sched.base import SchedulerBase


class FifoScheduler(SchedulerBase):
    name = "fifo"
    uses_bands = False

    def quantum_expired(self, pcb: PCB) -> bool:
        del pcb
        return False
