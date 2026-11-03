"""Cooperative scheduler loop. A quantum is settled when step returns, not inside it."""

from __future__ import annotations

import random
from pathlib import Path

from agentos.kernel.clock import VirtualClock
from agentos.kernel.config import Config
from agentos.kernel.context import ContextManager, Message
from agentos.kernel.events import Event, EventQueue
from agentos.kernel.pcb import (
    PCB,
    KernelError,
    ProcState,
    ProcTable,
    detach_wait,
    sleep,
    transition,
    wait_channel,
    wakeup,
)
from agentos.kernel.perm import PermTable
from agentos.kernel.runtime import Runtime
from agentos.kernel.sched.base import SchedulerBase, compute_effective_priority
from agentos.kernel.sched.fifo import FifoScheduler
from agentos.kernel.sched.rr import RRScheduler
from agentos.kernel.syscall import SyscallDispatcher
from agentos.kernel.tool import resolve_under_root
from agentos.kernel.trace import Trace
from agentos.kernel.types import ClassStats, ErrorClass, RunStats, StepKind, StepResult, TaskSpec
from agentos.llm.scripted import ScriptedBackend
from agentos.memory.store import MemoryStore

_TERMINAL = frozenset({ProcState.DONE, ProcState.FAILED})


class Kernel:
    def __init__(self, config: Config | None = None, policy: str = "fifo") -> None:
        self.config = config or Config()
        self.policy = policy
        self.clock = VirtualClock()
        self.table = ProcTable()
        self.events = EventQueue()
        self.trace = Trace(Path(self.config.trace_dir), self.clock)
        self.context = ContextManager(
            self.table, self.trace, self.config.CONTEXT_SOFT_CAP_TOKENS
        )
        self.memory = MemoryStore(Path(self.config.persistent_path))
        self.perm = PermTable()
        self.sched: SchedulerBase = _make_scheduler(policy, self.config)
        self.syscall = SyscallDispatcher(self)
        self.runtime = Runtime(self)
        self.llm = ScriptedBackend()
        self.rng_search = random.Random(self.config.EXPERIMENT_SEED)
        self.current: PCB | None = None
        self.yielded: list[PCB] = []
        self.queue_log: list[dict] = []
        self.terminal_ms: dict[int, int] = {}
        self.blocked_dep_pids: list[int] = []
        self._next_pid = 0
        self._arrived: set[int] = set()
        Path(self.config.tool_root).mkdir(parents=True, exist_ok=True)

    def submit(self, task: TaskSpec) -> PCB:
        if task.deps:
            raise KernelError("dependency join is not enabled")
        self._next_pid += 1
        pcb = PCB(
            pid=self._next_pid,
            state=ProcState.READY,
            agent_id=task.agent_id,
            task_class=task.task_class,
            base_priority=task.base_priority,
            effective_priority=task.base_priority,
            goal=task.goal,
            max_steps=task.max_steps,
            arrival_ms=task.arrival_ms,
            deadline_ms=task.deadline_ms,
            tool_forces={int(key): value for key, value in task.tool_forces.items()},
            deps=[],
        )
        self.table.procs[pcb.pid] = pcb
        self.context.create_initial(pcb)
        self.llm.register(pcb.pid, task.task_class, task.plan)
        self.events.push(Event(kind="arrive", at_ms=task.arrival_ms, pid=pcb.pid))
        return pcb

    def confirm(self, pid: int, allow: bool) -> None:
        pcb = self.table.procs[pid]
        if pcb.state is not ProcState.BLOCKED_CONFIRM or pcb.pending_syscall is None:
            raise KernelError("confirm of non-waiting proc")
        if not allow:
            self.finish(pcb, ProcState.FAILED, ErrorClass.PERM_DENY)
            return
        req = pcb.pending_syscall
        result = self.syscall.issue_tool(pcb, req)
        pcb.pending_syscall = None
        sleep(self.table, pcb, result.block_chan or "", ProcState.BLOCKED_TOOL, "tool")
        self.trace.sched_block(pcb)

    def run(self) -> RunStats:
        while self.has_unfinished():
            self.dispatch_due_events()
            self.flush_yielded()
            self._log("after_dispatch_flush")
            pcb = self.sched.next(self.clock.now_ms)
            if pcb is None:
                self._log("after_next", picked=None)
                if self.yielded:
                    raise KernelError("yielded task was not flushed")
                nxt = self.events.next_at()
                if nxt is None:
                    break
                delta = nxt - self.clock.now_ms
                if delta < 0:
                    raise KernelError("next event is in the past")
                if delta == 0:
                    raise KernelError("due event was not dispatched")
                self.account_ready_wait(delta)
                self.clock.advance(delta)
                self.trace.sched_idle(self.clock.now_ms, nxt)
                continue
            if pcb.queued_band is not None or pcb.pid in self.sched.queue_pids():
                raise KernelError(f"picked pid {pcb.pid} is still queued")
            self._log("after_next", picked=pcb.pid)
            prev = self.current
            if prev is None or prev.pid != pcb.pid:
                self.trace.sched_switch(prev, pcb, reason="pick")
            transition(pcb, ProcState.RUNNING)
            self.current = pcb
            pcb.wait_ms = 0
            self.recompute_priority(pcb)
            self.context.restore(pcb)
            result = self.runtime.step(pcb)
            self.context.save(pcb)
            self.context.clear_step_buffer()
            self.clock.advance(result.cpu_ms)
            self.account_ready_wait(result.cpu_ms)
            self.apply(pcb, result)
            self._log("after_apply", picked=pcb.pid)
        self.note_blocked_deps()
        return self._stats()

    def has_unfinished(self) -> bool:
        if self.yielded:
            return True
        if any(event.kind == "arrive" for event in self.events.pending()):
            return True
        return any(pcb.state not in _TERMINAL for pcb in self.table.procs.values())

    def dispatch_due_events(self) -> None:
        while True:
            event = self.events.pop_due(self.clock.now_ms)
            if event is None:
                return
            self._handle(event)

    def flush_yielded(self) -> None:
        pending = self.yielded
        self.yielded = []
        for pcb in pending:
            if pcb.state in _TERMINAL:
                continue
            self.sched.place(pcb, where="tail", previous_band=None)

    def apply(self, pcb: PCB, result: StepResult) -> None:
        pcb.tokens_this_quantum += result.tokens
        if result.was_llm:
            pcb.service_llm_ms += result.cpu_ms
            if result.error_class is ErrorClass.NONE:
                pcb.consecutive_llm_failures = 0

        if result.kind is StepKind.DONE:
            self.finish(pcb, ProcState.DONE, result.error_class)
            return
        if result.kind is StepKind.FAIL:
            self.finish(pcb, ProcState.FAILED, result.error_class)
            return
        if result.kind is StepKind.BLOCK:
            if not result.block_chan:
                raise KernelError(f"pid {pcb.pid} blocked without a channel")
            self.clear_io_boost(pcb)
            self.recompute_priority(pcb)
            sleep(
                self.table,
                pcb,
                result.block_chan,
                result.next_state,
                result.block_reason or "",
            )
            pcb.not_before_ms = result.not_before_ms
            self.trace.sched_block(pcb)
            return

        band_before_place = pcb.effective_priority
        if result.error_class is ErrorClass.LLM_PARSE:
            pcb.consecutive_llm_failures += 1
            if pcb.consecutive_llm_failures >= self.config.K:
                self.finish(pcb, ProcState.FAILED, ErrorClass.LLM_PARSE)
                return
            if self.sched.uses_bands:
                self.note_penalty(pcb, cause="llm_parse")

        if self.sched.quantum_expired(pcb):
            spent = pcb.tokens_this_quantum
            pcb.tokens_this_quantum = 0
            self.clear_io_boost(pcb)
            self.recompute_priority(pcb)
            transition(pcb, ProcState.READY)
            self.yielded.append(pcb)
            self.trace.sched_yield(pcb, reason="quantum", tokens_this_quantum=spent)
            return
        if self.sched.preempted_by_higher(pcb):
            self.clear_io_boost(pcb)
            self.recompute_priority(pcb)
            transition(pcb, ProcState.READY)
            self.sched.place(pcb, where="head", previous_band=band_before_place)
            self.trace.sched_yield(
                pcb, reason="preempt", tokens_this_quantum=pcb.tokens_this_quantum
            )
            return
        transition(pcb, ProcState.READY)
        self.sched.place(pcb, where="head", previous_band=band_before_place)

    def finish(self, pcb: PCB, state: ProcState, reason: ErrorClass) -> None:
        if pcb.queued_band is not None:
            self.sched.dequeue(pcb)
        self.yielded = [item for item in self.yielded if item.pid != pcb.pid]
        detach_wait(self.table, pcb)
        pcb.not_before_ms = None
        pcb.io_boost_pending = False
        pcb.pending_syscall = None
        transition(pcb, state)
        pcb.exit_reason = reason.name
        self.terminal_ms[pcb.pid] = self.clock.now_ms
        self.trace.task_terminal(pcb)
        if reason is ErrorClass.GOAL_DONE:
            self.memory.append_completed(pcb, self.terminal_ms[pcb.pid])

    def note_penalty(self, pcb: PCB, cause: str) -> None:
        old = pcb.effective_priority
        self.recompute_priority(pcb)
        self.trace.priority_decay(pcb, old, pcb.effective_priority, cause)

    def clear_io_boost(self, pcb: PCB) -> None:
        pcb.io_boost_pending = False

    def recompute_priority(self, pcb: PCB) -> None:
        pcb.effective_priority = compute_effective_priority(pcb, self.config)

    def account_ready_wait(self, delta: int) -> None:
        if delta <= 0:
            return
        for pcb in list(self.table.procs.values()):
            if pcb.state is not ProcState.READY or pcb.queued_band is None:
                continue
            pcb.wait_ms += delta
            old = pcb.effective_priority
            self.recompute_priority(pcb)
            if self.sched.uses_bands and self.sched.relocate_ready(pcb) and pcb.effective_priority > old:
                self.trace.priority_boost(pcb, old, pcb.effective_priority, "aging")

    def note_blocked_deps(self) -> None:
        self.blocked_dep_pids = [
            pcb.pid for pcb in self.table.procs.values() if pcb.state is ProcState.BLOCKED_DEP
        ]

    def _handle(self, event: Event) -> None:
        if event.kind == "arrive":
            self._admit(event.pid)
            return
        if event.kind == "tool_done":
            self._on_tool_done(event)
            return
        if event.kind == "tool_backoff_done":
            self._on_tool_backoff_done(event)
            return
        if event.kind == "resource_ready":
            self._on_resource_ready(event)
            return
        raise KernelError(f"unknown event {event.kind}")

    def _admit(self, pid: int) -> None:
        if pid in self._arrived:
            return
        self._arrived.add(pid)
        pcb = self.table.procs[pid]
        self.trace.task_arrive(pcb)
        if pcb.state in _TERMINAL or pcb.queued_band is not None:
            return
        self.recompute_priority(pcb)
        self.sched.place(pcb, where="tail", previous_band=None)

    def _on_tool_done(self, event: Event) -> None:
        pcb = self.table.procs[event.pid]
        pcb.service_tool_ms += event.service_ms
        self.trace.tool_done(pcb, event.at_ms, bool(event.ok), event.issued_step)
        if event.ok:
            observation = self._tool_observation(pcb, event)
            self.context.append_observation(
                pcb.pid, Message("tool", observation, event.issued_step, 0)
            )
            pcb.last_observation = observation
            pcb.consecutive_tool_failures = 0
            pcb.io_boost_pending = True
            old = pcb.effective_priority
            self.recompute_priority(pcb)
            if self.sched.uses_bands and pcb.effective_priority > old:
                self.trace.priority_boost(pcb, old, pcb.effective_priority, "io_return")
            from_state = pcb.state.value
            detach_wait(self.table, pcb)
            transition(pcb, ProcState.READY)
            pcb.wait_ms = 0
            pcb.not_before_ms = None
            self.sched.place(pcb, where="tail", previous_band=None)
            self.trace.sched_wakeup(pcb, event_chan(pcb, event), from_state)
            return
        delay = self.config.TOOL_BACKOFF_MS
        self.events.push(
            Event(
                kind="tool_backoff_done",
                at_ms=self.clock.now_ms + delay,
                pid=pcb.pid,
                issued_step=event.issued_step,
                tool_name=event.tool_name,
            )
        )
        pcb.waiting_reason = "tool_backoff"
        pcb.not_before_ms = self.clock.now_ms + delay

    def _on_tool_backoff_done(self, event: Event) -> None:
        pcb = self.table.procs[event.pid]
        if pcb.state in _TERMINAL:
            return
        pcb.consecutive_tool_failures += 1
        if pcb.consecutive_tool_failures >= self.config.K:
            self.finish(pcb, ProcState.FAILED, ErrorClass.TOOL_FAIL)
            return
        if self.sched.uses_bands:
            self.note_penalty(pcb, cause="tool_fail")
        from_state = pcb.state.value
        detach_wait(self.table, pcb)
        transition(pcb, ProcState.READY)
        pcb.wait_ms = 0
        pcb.not_before_ms = None
        self.sched.place(pcb, where="tail", previous_band=None)
        self.trace.sched_wakeup(pcb, f"tool:{pcb.pid}:{event.issued_step}", from_state)

    def _on_resource_ready(self, event: Event) -> None:
        chan = f"resource:{event.pid}"
        pids = list(self.table.wait.get(chan, []))
        from_states = {pid: self.table.procs[pid].state.value for pid in pids}
        woken = wakeup(self.table, self.sched, chan, self.clock.now_ms)
        for pcb in woken:
            self.trace.sched_wakeup(pcb, chan, from_states[pcb.pid])

    def _tool_observation(self, pcb: PCB, event: Event) -> str:
        if event.tool_name == "read_local":
            path = resolve_under_root(Path(self.config.tool_root), str(event.tool_args.get("path", "")))
            if not path.is_file():
                return ""
            return path.read_text(encoding="utf-8")
        if event.tool_name == "search":
            return f"search-result-{pcb.pid}-{event.issued_step}"
        if event.tool_name == "write_report":
            reports = Path(self.config.tool_root) / "reports"
            reports.mkdir(parents=True, exist_ok=True)
            content = str(event.tool_args.get("content", ""))
            (reports / f"{pcb.pid}.txt").write_text(content, encoding="utf-8")
            return content[:80]
        return ""

    def _log(self, phase: str, picked: int | None = None) -> None:
        self.queue_log.append(
            {
                "now_ms": self.clock.now_ms,
                "phase": phase,
                "queue": self.sched.queue_pids(),
                "yielded": [pcb.pid for pcb in self.yielded],
                "picked": picked,
            }
        )

    def _stats(self) -> RunStats:
        buckets: dict[str, list[int]] = {}
        for pcb in self.table.procs.values():
            if pcb.pid not in self.terminal_ms:
                continue
            buckets.setdefault(pcb.task_class, []).append(self.terminal_ms[pcb.pid] - pcb.arrival_ms)
        by_class = {
            name: ClassStats(n=len(samples), mean_response_ms=sum(samples) / len(samples))
            for name, samples in buckets.items()
        }
        return RunStats(
            context_mismatch=self.context.mismatch,
            by_class=by_class,
            trace_path=str(self.trace.path),
            blocked_dep_pids=list(self.blocked_dep_pids),
        )


def event_chan(pcb: PCB, event: Event) -> str:
    if event.tool_name:
        return f"tool:{pcb.pid}:{event.issued_step}"
    return wait_channel("tool", pcb)


def _make_scheduler(policy: str, cfg: Config) -> SchedulerBase:
    if policy in ("fifo", "fcfs"):
        return FifoScheduler()
    if policy == "rr":
        return RRScheduler(cfg)
    raise KernelError(f"unknown policy {policy}")
