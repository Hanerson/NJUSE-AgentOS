"""Syscall path: validate, permission, then issue. issue does not advance the clock."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentos.kernel.context import Message
from agentos.kernel.events import Event
from agentos.kernel.pcb import PCB, ProcState, SyscallReq, wait_channel
from agentos.kernel.perm import Decision
from agentos.kernel.tool import resolve_under_root
from agentos.kernel.types import ErrorClass, StepKind, StepResult

if TYPE_CHECKING:
    from agentos.kernel.loop import Kernel


class SyscallDispatcher:
    def __init__(self, kernel: Kernel) -> None:
        self.k = kernel

    def dispatch(self, pcb: PCB, req: SyscallReq) -> StepResult:
        self.k.trace.syscall_enter(pcb, req.name)
        invalid = self._validate_shape(req)
        if invalid is not None:
            self._exit(pcb, req.name, invalid)
            return invalid
        decision = self.k.perm.check(pcb.agent_id, req.name)
        self.k.trace.perm_check(pcb, req.name, decision.value)
        if req.name == "recall":
            result = self._recall(pcb, req, decision)
            self._exit(pcb, req.name, result)
            return result
        if decision is Decision.DENY:
            result = self._deny(req.name)
            self._exit(pcb, req.name, result)
            return result
        if req.name == "read_local":
            try:
                resolve_under_root(self.k.config.tool_root, str(req.args.get("path", "")))
            except PermissionError:
                result = self._deny(req.name)
                self._exit(pcb, req.name, result)
                return result
        if decision is Decision.CONFIRM:
            result = StepResult(
                kind=StepKind.BLOCK,
                error_class=ErrorClass.NONE,
                tokens=0,
                cpu_ms=0,
                next_state=ProcState.BLOCKED_CONFIRM,
                observation_summary=req.name,
                block_reason="confirm",
                block_chan=wait_channel("confirm", pcb),
            )
            self._exit(pcb, req.name, result)
            return result
        result = self.issue_tool(pcb, req)
        self._exit(pcb, req.name, result)
        return result

    def issue_tool(self, pcb: PCB, req: SyscallReq) -> StepResult:
        service = {
            "read_local": self.k.config.READ_LOCAL_SERVICE_MS,
            "search": self.k.config.SEARCH_SERVICE_MS,
            "write_report": self.k.config.WRITE_REPORT_SERVICE_MS,
        }[req.name]
        self.k.events.push(
            Event(
                kind="tool_done",
                at_ms=self.k.clock.now_ms + service,
                pid=pcb.pid,
                ok=self._tool_ok(pcb, req),
                issued_step=pcb.step_index,
                service_ms=service,
                tool_name=req.name,
                tool_args=dict(req.args),
            )
        )
        return StepResult(
            kind=StepKind.BLOCK,
            error_class=ErrorClass.NONE,
            tokens=0,
            cpu_ms=0,
            next_state=ProcState.BLOCKED_TOOL,
            observation_summary=req.name,
            block_reason="tool",
            block_chan=wait_channel("tool", pcb),
        )

    def _tool_ok(self, pcb: PCB, req: SyscallReq) -> bool:
        forced = pcb.tool_forces.get(pcb.step_index)
        if forced is not None:
            return forced == "ok"
        if pcb.task_class == "flaky" and req.name == "search":
            return self.k.rng_search.random() >= self.k.config.SEARCH_FAIL_P
        return True

    def _recall(self, pcb: PCB, req: SyscallReq, decision: Decision) -> StepResult:
        if decision is Decision.DENY:
            return self._deny("recall")
        if decision is Decision.CONFIRM:
            return StepResult(
                kind=StepKind.BLOCK,
                error_class=ErrorClass.NONE,
                tokens=0,
                cpu_ms=0,
                next_state=ProcState.BLOCKED_CONFIRM,
                observation_summary="recall",
                block_reason="confirm",
                block_chan=wait_channel("confirm", pcb),
            )
        query = req.args.get("query", "")
        if not isinstance(query, str):
            return StepResult(
                kind=StepKind.CONTINUE,
                error_class=ErrorClass.LLM_PARSE,
                tokens=0,
                cpu_ms=0,
                next_state=ProcState.READY,
                observation_summary="recall",
            )
        text = self.k.memory.read_recent(query=query, limit=20)
        self.k.context.append_to_step_buffer(
            pcb.pid, Message("tool", text, pcb.step_index, 0)
        )
        pcb.last_observation = text
        return StepResult(
            kind=StepKind.CONTINUE,
            error_class=ErrorClass.NONE,
            tokens=0,
            cpu_ms=self.k.config.RECALL_CPU_MS,
            next_state=ProcState.READY,
            observation_summary=text[:80],
        )

    def _validate_shape(self, req: SyscallReq) -> StepResult | None:
        if not isinstance(req.args, dict):
            return self._parse(req.name)
        if req.name == "read_local":
            path = req.args.get("path")
            if not isinstance(path, str) or path == "":
                return self._parse(req.name)
        elif req.name == "write_report":
            content = req.args.get("content", "")
            if not isinstance(content, str) or len(content) > 4096:
                return self._parse(req.name)
        elif req.name == "recall" and "query" in req.args and not isinstance(req.args["query"], str):
            return self._parse(req.name)
        elif req.name == "search" and "query" in req.args and not isinstance(req.args.get("query", ""), str):
            return self._parse(req.name)
        return None

    def _parse(self, name: str) -> StepResult:
        return StepResult(
            kind=StepKind.CONTINUE,
            error_class=ErrorClass.LLM_PARSE,
            tokens=0,
            cpu_ms=0,
            next_state=ProcState.READY,
            observation_summary=name,
        )

    def _deny(self, name: str) -> StepResult:
        return StepResult(
            kind=StepKind.FAIL,
            error_class=ErrorClass.PERM_DENY,
            tokens=0,
            cpu_ms=0,
            next_state=ProcState.FAILED,
            observation_summary=name,
        )

    def _exit(self, pcb: PCB, name: str, result: StepResult) -> None:
        self.k.trace.syscall_exit(
            pcb,
            name,
            result.error_class.value,
            result.next_state.value,
            result.observation_summary,
        )
