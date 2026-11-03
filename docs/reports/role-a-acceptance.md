# Role A（内核）验收记录

依据：[分工表](../reference/ref.png) 与 [设计文档](../design/AgentOS-调度内核设计.md) 的 PR Plan。
检查日期：2026-11-03。

## 结论

Role A 负责的内容**已完成**，验收 1 通过，相关测试全部通过。

```text
python -m pytest -q
19 passed in 0.15s
```

## 需求对照

| 分工要求 | 实现 | 验证 |
| --- | --- | --- |
| PCB 与状态机 | [pcb.py](../../agentos/kernel/pcb.py)：`ProcState` 八种状态、`_TRANSITIONS` 合法边、`sleep`/`wakeup`/`detach_wait`、`ProcTable` | `tests/test_pcb_states.py`（9 项） |
| 虚拟时钟 | [clock.py](../../agentos/kernel/clock.py)：`VirtualClock.advance` 禁止回拨，内核中无真实睡眠 | `test_clock_refuses_to_go_backwards`、`tests/test_no_sleep.py` |
| 主循环（PR4 的循环部分） | [loop.py](../../agentos/kernel/loop.py)：`dispatch_due_events → flush_yielded → sched.next → runtime.step → clock.advance → apply`；空闲时跳到下一事件 | `tests/test_issue_clock.py`；验收 1 |
| FCFS | [fifo.py](../../agentos/kernel/sched/fifo.py)：步边界回队头，量子被忽略；[base.py](../../agentos/kernel/sched/base.py) 单队列不因优先级带强制尾插 | `tests/test_fifo_convoy.py`（5 项） |
| Round-Robin | [rr.py](../../agentos/kernel/sched/rr.py)：`QUANTUM_TOKENS=256`，步边界结账；量子用尽进 `yielded`，循环顶部先 dispatch 再 flush | `tests/test_rr_quantum.py` 验收 1 |
| 工具等待重叠（PR5） | FCFS 下 A 阻塞期间 B 占用模型 | `tests/test_tool_overlap.py`、`tests/test_snapshot_owner.py` |

## 验收 1 结果

场景：RR，长任务 L（`arrival_ms=0`，`max_steps=8`，三步计划），短任务 S（`arrival_ms=160`，`max_steps=2`，一条 finish）。

`demo_preempt` 实际输出：

```text
L state=DONE step=5 exit=GOAL_DONE
S state=DONE exit=GOAL_DONE
t=160 queue=[2, 1] yielded=[]        # [S, L]
```

t=160 队列日志：

```text
after_apply            queue=[]    yielded=[1]  picked=1   # L 量子用尽
after_dispatch_flush   queue=[2,1] yielded=[]   picked=None # S 到达 + L 尾插
after_next             queue=[1]   yielded=[]   picked=2   # 选中 S
```

`tests/test_rr_quantum.py` 进一步断言：

- t=160 的 `after_dispatch_flush` 队列为 `[S, L]`；
- L 的 `sched_yield(reason=quantum, step_index=3)` 早于 S 的 `task_terminal`，后者早于 L 的 `context_restore` 与第 4 步 `llm_generate`；
- 恢复后的上下文中仍有 `OBS_STEP2` 工具消息，且 `step_index == 2`；
- 全程 `context_mismatch == 0`，无 `priority_decay`。

## 说明

- 本次只检查 Role A 范围；`runtime.py`、`context.py`、`syscall.py` 等属于 Role B，但已存在并支撑 Role A 的测试。
- 未实现项（`sched/fail_priority.py`、`workloads/mix.py`、`experiments/run_sched.py`、`llm/http.py`）属于 Role C/D，不影响 Role A 验收。
