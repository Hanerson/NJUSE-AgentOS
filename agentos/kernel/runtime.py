"""One syscall per step. The plan is not run to completion inside here."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentos.kernel.context import Message
from agentos.kernel.events import Event
from agentos.kernel.pcb import PCB, ProcState, SyscallReq, wait_channel
from agentos.kernel.types import ErrorClass, StepKind, StepResult

if TYPE_CHECKING:
    from agentos.kernel.loop import Kernel


class Runtime:
    def __init__(self, kernel: Kernel) -> None:
        self.k = kernel

    def step(self, pcb: PCB) -> StepResult:
        now = self.k.clock.now_ms
        if pcb.deadline_ms is not None and now >= pcb.deadline_ms:
            return StepResult(StepKind.FAIL, ErrorClass.TIMEOUT, 0, 0, ProcState.FAILED, "")
        if pcb.step_index >= pcb.max_steps:
            return StepResult(StepKind.DONE, ErrorClass.MAX_STEPS, 0, 0, ProcState.DONE, "")

        pcb.step_index += 1
        if pcb.pending_syscall is not None:
            req = pcb.pending_syscall
            result = self.k.syscall.dispatch(pcb, req)
            if result.next_state is not ProcState.BLOCKED_CONFIRM:
                pcb.pending_syscall = None
            return result

        self.k.trace.syscall_enter(pcb, "llm_generate")
        llm_result = self.k.llm.generate(self.k.context.snapshot_view(pcb.pid))
        if hasattr(self.k.llm, "cursors"):
            pcb.plan_cursor = self.k.llm.cursors.get(pcb.pid, pcb.plan_cursor)
        error = llm_result.error_class or ErrorClass.NONE

        if error is ErrorClass.LLM_RESOURCE:
            backoff = self.k.config.LLM_RESOURCE_BACKOFF_MS
            self.k.events.push(
                Event(kind="resource_ready", at_ms=now + backoff, pid=pcb.pid)
            )
            result = StepResult(
                kind=StepKind.BLOCK,
                error_class=ErrorClass.LLM_RESOURCE,
                tokens=0,
                cpu_ms=self.k.config.RESOURCE_FAIL_CPU_MS,
                next_state=ProcState.BLOCKED_RESOURCE,
                observation_summary="llm_resource",
                block_reason="llm_resource",
                block_chan=wait_channel("resource", pcb),
                not_before_ms=now + backoff,
                was_llm=True,
            )
            self._exit(pcb, result)
            return result
        if error is ErrorClass.LLM_HTTP:
            result = StepResult(
                kind=StepKind.FAIL,
                error_class=ErrorClass.LLM_HTTP,
                tokens=0,
                cpu_ms=0,
                next_state=ProcState.FAILED,
                observation_summary="llm_http",
                was_llm=True,
            )
            self._exit(pcb, result)
            return result
        if error is ErrorClass.LLM_PARSE:
            result = StepResult(
                kind=StepKind.CONTINUE,
                error_class=ErrorClass.LLM_PARSE,
                tokens=llm_result.tokens,
                cpu_ms=llm_result.cpu_ms,
                next_state=ProcState.READY,
                observation_summary="llm_parse",
                was_llm=True,
            )
            self._exit(pcb, result)
            return result

        action = llm_result.action
        if action is None:
            result = StepResult(
                kind=StepKind.CONTINUE,
                error_class=ErrorClass.LLM_PARSE,
                tokens=llm_result.tokens,
                cpu_ms=llm_result.cpu_ms,
                next_state=ProcState.READY,
                observation_summary="llm_parse",
                was_llm=True,
            )
            self._exit(pcb, result)
            return result
        buf = self.k.context.step_buffer(pcb.pid)
        buf.messages.append(Message("assistant", action.raw_text, pcb.step_index, llm_result.tokens))

        if action.type == "finish":
            pcb.exit_summary = action.summary
            result = StepResult(
                kind=StepKind.DONE,
                error_class=ErrorClass.GOAL_DONE,
                tokens=llm_result.tokens,
                cpu_ms=llm_result.cpu_ms,
                next_state=ProcState.DONE,
                observation_summary=action.summary or "",
                was_llm=True,
            )
            self._exit(pcb, result)
            return result
        if action.type == "think":
            result = StepResult(
                kind=StepKind.CONTINUE,
                error_class=ErrorClass.NONE,
                tokens=llm_result.tokens,
                cpu_ms=llm_result.cpu_ms,
                next_state=ProcState.READY,
                observation_summary="think",
                was_llm=True,
            )
            self._exit(pcb, result)
            return result
        pcb.pending_syscall = SyscallReq(action.name or "", dict(action.args))
        result = StepResult(
            kind=StepKind.CONTINUE,
            error_class=ErrorClass.NONE,
            tokens=llm_result.tokens,
            cpu_ms=llm_result.cpu_ms,
            next_state=ProcState.READY,
            observation_summary=action.name or "tool",
            was_llm=True,
        )
        self._exit(pcb, result)
        return result

    def _exit(self, pcb: PCB, result: StepResult) -> None:
        # syscall_exit records the model result class. GOAL_DONE stays on task_terminal.
        error = result.error_class
        if error is ErrorClass.GOAL_DONE:
            reported = ErrorClass.NONE
        else:
            reported = error
        self.k.trace.syscall_exit(
            pcb,
            "llm_generate",
            reported.value,
            result.next_state.value,
            result.observation_summary,
        )
