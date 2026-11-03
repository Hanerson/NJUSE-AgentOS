"""PR1: process table, legal transitions, virtual clock, frozen constants."""

from __future__ import annotations

import pytest

from agentos.kernel import config
from agentos.kernel.clock import VirtualClock
from agentos.kernel.pcb import (
    KernelError,
    ProcState,
    sleep,
    transition,
    wait_channel,
    wakeup,
)
from tests.conftest import make_kernel, spec


def test_submit_with_empty_deps_is_ready(tmp_path) -> None:
    kernel = make_kernel(tmp_path)
    pcb = kernel.submit(spec())
    assert pcb.state is ProcState.READY
    assert pcb.pid in kernel.table.procs
    assert pcb.queued_band is None
    assert pcb.deps == []


def test_nonempty_deps_rejected_without_blocked_dep(tmp_path) -> None:
    kernel = make_kernel(tmp_path)
    with pytest.raises(KernelError, match="dependency join is not enabled"):
        kernel.submit(spec(deps=[1]))
    assert kernel.table.procs == {}
    assert all(pcb.state is not ProcState.BLOCKED_DEP for pcb in kernel.table.procs.values())


def test_illegal_transition_raises(tmp_path) -> None:
    kernel = make_kernel(tmp_path)
    pcb = kernel.submit(spec())
    with pytest.raises(KernelError, match="illegal transition"):
        transition(pcb, ProcState.DONE)
    transition(pcb, ProcState.RUNNING)
    transition(pcb, ProcState.DONE)
    with pytest.raises(KernelError, match="illegal transition"):
        transition(pcb, ProcState.READY)
    with pytest.raises(KernelError, match="illegal transition"):
        transition(pcb, ProcState.BLOCKED_DEP)


def test_legal_block_and_wake_edges(tmp_path) -> None:
    kernel = make_kernel(tmp_path)
    pcb = kernel.submit(spec())
    transition(pcb, ProcState.RUNNING)
    sleep(kernel.table, pcb, wait_channel("tool", pcb), ProcState.BLOCKED_TOOL, "tool")
    assert pcb.wait_chan == f"tool:{pcb.pid}:{pcb.step_index}"
    woken = wakeup(kernel.table, kernel.sched, pcb.wait_chan, 0)
    assert woken == [pcb]
    assert pcb.state is ProcState.READY
    assert pcb.wait_chan is None


def test_sleep_detaches_previous_channel(tmp_path) -> None:
    kernel = make_kernel(tmp_path)
    pcb = kernel.submit(spec())
    transition(pcb, ProcState.RUNNING)
    sleep(kernel.table, pcb, wait_channel("confirm", pcb), ProcState.BLOCKED_CONFIRM, "confirm")
    assert list(kernel.table.wait) == [f"confirm:{pcb.pid}"]
    pcb.step_index = 2
    sleep(kernel.table, pcb, wait_channel("tool", pcb), ProcState.BLOCKED_TOOL, "tool")
    assert f"confirm:{pcb.pid}" not in kernel.table.wait
    assert kernel.table.wait == {f"tool:{pcb.pid}:2": [pcb.pid]}
    assert pcb.wait_chan == f"tool:{pcb.pid}:2"


def test_wakeup_of_running_proc_raises(tmp_path) -> None:
    kernel = make_kernel(tmp_path)
    pcb = kernel.submit(spec())
    transition(pcb, ProcState.RUNNING)
    chan = wait_channel("resource", pcb)
    kernel.table.wait[chan] = [pcb.pid]
    pcb.wait_chan = chan
    with pytest.raises(KernelError, match="wakeup of non-blocked"):
        wakeup(kernel.table, kernel.sched, chan, kernel.clock.now_ms)


def test_wakeup_places_on_fifo_tail(tmp_path) -> None:
    kernel = make_kernel(tmp_path)
    pcb = kernel.submit(spec())
    transition(pcb, ProcState.RUNNING)
    chan = wait_channel("resource", pcb)
    sleep(kernel.table, pcb, chan, ProcState.BLOCKED_RESOURCE, "llm_resource")
    woken = wakeup(kernel.table, kernel.sched, chan, 0)
    assert woken == [pcb]
    assert pcb.state is ProcState.READY
    assert pcb.wait_chan is None
    assert pcb.queued_band == 0
    assert kernel.sched.queue_pids() == [pcb.pid]
    assert chan not in kernel.table.wait


def test_clock_refuses_to_go_backwards() -> None:
    clock = VirtualClock()
    clock.advance(5)
    assert clock.now_ms == 5
    clock.advance(0)
    assert clock.now_ms == 5
    with pytest.raises(KernelError, match="clock went backwards"):
        clock.advance(-1)
    assert clock.now_ms == 5


def test_constants_match_the_design() -> None:
    assert config.QUANTUM_TOKENS == 256
    assert config.PRIO_MIN == 0
    assert config.PRIO_MAX == 4
    assert config.DEFAULT_BASE_PRIORITY == 2
    assert (config.A, config.B, config.C) == (1, 1, 1)
    assert config.K == 3
    assert config.AGING_QUANTUM_MS == 500
    assert config.AGING_CAP == 2
    assert config.LLM_RESOURCE_BACKOFF_MS == 200
    assert config.TOOL_BACKOFF_MS == 400
    assert config.SEARCH_SERVICE_MS == 500
    assert config.READ_LOCAL_SERVICE_MS == 20
    assert config.WRITE_REPORT_SERVICE_MS == 150
    assert config.RECALL_CPU_MS == 5
    assert config.SHORT_LLM_TOKENS == 64
    assert config.LONG_LLM_TOKENS == 160
    assert config.CONTEXT_SOFT_CAP_TOKENS == 4096
    cfg = config.Config()
    assert cfg.K == 3
    assert cfg.QUANTUM_TOKENS == 256
    assert cfg.enable_fail_penalty is True
    assert cfg.enable_io_boost is True
