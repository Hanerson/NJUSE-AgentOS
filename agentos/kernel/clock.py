"""Virtual clock. The scheduler advances it. Nothing in the kernel sleeps."""

from __future__ import annotations

from agentos.kernel.pcb import KernelError


class VirtualClock:
    def __init__(self) -> None:
        self.now_ms = 0

    def advance(self, ms: int) -> None:
        if ms < 0:
            raise KernelError("clock went backwards")
        self.now_ms += ms
