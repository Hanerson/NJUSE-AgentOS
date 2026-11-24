"""Acceptance 3. Failure is visible, and only the failure-aware policy decays.

The scenario is deterministic: two short tasks each finish in one model step, and
one flaky task fails its ``search`` at tool steps 2, 4 and 6 through ``tool_forces``.
No random number is drawn here.
"""

from __future__ import annotations

from pathlib import Path

from agentos.kernel.config import Config
from agentos.kernel.loop import Kernel
from agentos.kernel.pcb import PCB, ProcState
from agentos.kernel.types import TaskSpec

SHORT_PLAN = [
    {
        "op": "llm",
        "tokens": 64,
        "cpu_ms": 50,
        "action": {"type": "finish", "summary": "short-done"},
    }
]

# Tool steps, not model steps: step 2 issues the first search, step 4 the second,
# step 6 the third.  The third backoff sends the process to FAILED without decay.
FAILING_TOOL_STEPS = {2: "fail", 4: "fail", 6: "fail"}


def build_fail(root: Path, policy: str = "fail") -> tuple[Kernel, PCB, PCB, PCB]:
    """Two shorts and one flaky, all arriving at t=0, submitted in that order."""
    kernel = Kernel(
        config=Config(
            tool_root=root / "tool_root",
            persistent_path=root / "memory" / "completed.jsonl",
            trace_dir=root / "trace",
        ),
        policy=policy,
    )
    short_a = kernel.submit(
        TaskSpec(
            agent_id="short-a",
            task_class="short",
            goal="finish immediately",
            max_steps=4,
            base_priority=2,
            arrival_ms=0,
            plan=SHORT_PLAN,
        )
    )
    short_b = kernel.submit(
        TaskSpec(
            agent_id="short-b",
            task_class="short",
            goal="finish immediately",
            max_steps=4,
            base_priority=2,
            arrival_ms=0,
            plan=SHORT_PLAN,
        )
    )
    flaky = kernel.submit(
        TaskSpec(
            agent_id="flaky",
            task_class="flaky",
            goal="search until the kernel stops it",
            max_steps=12,
            base_priority=2,
            arrival_ms=0,
            plan=[],
            tool_forces=dict(FAILING_TOOL_STEPS),
        )
    )
    return kernel, short_a, short_b, flaky


def decays(kernel: Kernel) -> list[dict]:
    return [row for row in kernel.trace.events if row["event"] == "priority_decay"]


def terminal_of(kernel: Kernel, pid: int) -> dict:
    return next(
        row
        for row in kernel.trace.events
        if row["event"] == "task_terminal" and row["pid"] == pid
    )


def run_pair(root: Path = Path("var/demo_fail")) -> dict[str, tuple[Kernel, PCB]]:
    """Run the same scenario under FCFS and fail, once each."""
    runs: dict[str, tuple[Kernel, PCB]] = {}
    for policy in ("fifo", "fail"):
        kernel, _, _, flaky = build_fail(root / policy, policy=policy)
        kernel.run()
        runs[policy] = (kernel, flaky)
    return runs


def check_acceptance_3(root: Path = Path("var/demo_fail")) -> dict[str, tuple[Kernel, PCB]]:
    """FCFS has no decay but still stops at K; fail decays first, then stops at K."""
    runs = run_pair(root)
    fifo, fifo_flaky = runs["fifo"]
    fail, flaky = runs["fail"]

    assert decays(fifo) == [], "FCFS must not emit priority_decay"
    assert fifo_flaky.state is ProcState.FAILED
    assert fifo_flaky.exit_reason == "TOOL_FAIL"
    assert terminal_of(fifo, fifo_flaky.pid)["exit_reason"] == "TOOL_FAIL"

    rows = decays(fail)
    assert [(row["old"], row["new"], row["cause"]) for row in rows] == [
        (2, 1, "tool_fail"),
        (1, 0, "tool_fail"),
    ], rows
    terminal = terminal_of(fail, flaky.pid)
    assert terminal["state"] == "FAILED"
    assert terminal["exit_reason"] == "TOOL_FAIL"
    assert flaky.state is ProcState.FAILED
    assert flaky.queued_band is None
    terminal_at = fail.trace.events.index(terminal)
    assert fail.trace.events.index(rows[-1]) < terminal_at
    # The third failure is the terminal one: no decay or scheduling follows it.
    assert all(
        row["event"] != "priority_decay" and row.get("pid") != flaky.pid
        for row in fail.trace.events[terminal_at + 1 :]
    )
    return runs


def main() -> None:
    runs = check_acceptance_3()
    for policy in ("fifo", "fail"):
        kernel, flaky = runs[policy]
        causes = [(row["old"], row["new"], row["cause"]) for row in decays(kernel)]
        print(
            f"{policy}: flaky state={flaky.state.value} exit={flaky.exit_reason} "
            f"decays={causes}"
        )


if __name__ == "__main__":
    main()
