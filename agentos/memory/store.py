"""Append-only completed.jsonl. Failures are not recorded."""

from __future__ import annotations

import json
from pathlib import Path

from agentos.kernel.pcb import PCB


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append_completed(self, pcb: PCB, finished_ms: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "pid": pcb.pid,
            "agent_id": pcb.agent_id,
            "task_class": pcb.task_class,
            "summary": pcb.exit_summary,
            "finished_ms": finished_ms,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def read_recent(self, query: str = "", limit: int = 20) -> str:
        if not self.path.exists():
            return "[]"
        rows: list[dict] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if query and query not in line:
                continue
            rows.append(row)
        return json.dumps(rows[-limit:], ensure_ascii=False)
