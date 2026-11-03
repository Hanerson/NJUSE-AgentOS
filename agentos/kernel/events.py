"""Min-heap of arrive, tool, and backoff events. seq keeps one timestamp stable."""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field


@dataclass
class Event:
    kind: str
    at_ms: int
    pid: int
    seq: int = 0
    ok: bool | None = None
    issued_step: int = 0
    service_ms: int = 0
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)


class EventQueue:
    def __init__(self) -> None:
        self._heap: list[tuple[int, int, int]] = []
        self._events: dict[int, Event] = {}
        self._seq = 0

    def push(self, event: Event) -> Event:
        self._seq += 1
        event.seq = self._seq
        self._events[self._seq] = event
        heapq.heappush(self._heap, (event.at_ms, event.seq, event.seq))
        return event

    def pop_due(self, now_ms: int) -> Event | None:
        if not self._heap or self._heap[0][0] > now_ms:
            return None
        _at, seq, _same = heapq.heappop(self._heap)
        return self._events.pop(seq)

    def peek(self) -> Event | None:
        if not self._heap:
            return None
        return self._events[self._heap[0][2]]

    def next_at(self) -> int | None:
        if not self._heap:
            return None
        return self._heap[0][0]

    def pending(self) -> list[Event]:
        return [self._events[seq] for _at, seq, _same in self._heap]
