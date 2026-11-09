# Role C（策略与实验）验收记录

依据：[分工表](../reference/ref.png)、[设计文档](../design/AgentOS-调度内核设计.md) 的 PR7/PR8 与验收 3。
检查日期：2026-11-06。

## 结论

Role C 负责的内容**已完成**：

- 失败感知优先级调度器、老化、I/O 加分、失败惩罚与 K=3 截断；
- `--no-fail-penalty` / `--no-io-boost` 两个消融开关的语义与测试；
- 固定种子的 40 任务混合负载 `workloads/mix.py`；
- `wasted_llm_tokens` / `retry_llm_tokens` / p95 / `short_held_selections` 等指标口径与可调用实现；
- 验收 3 脚本 `demo_fail.py`。

```text
python -m pytest -q
47 passed
```

其中 28 个是本次新增的 C 范围测试；原有 `test_fifo_convoy`、`test_tool_overlap`、`test_rr_quantum` 等仍通过。

## 需求对照

| 分工要求 | 实现 | 验证 |
| --- | --- | --- |
| 失败感知优先级 | [fail_priority.py](../../agentos/kernel/sched/fail_priority.py)：五带、带内 RR、量子 256；[base.py](../../agentos/kernel/sched/base.py) 负责公式与换带 | `tests/test_fail_priority.py`（13 项） |
| 优先级公式 | `compute_effective_priority`：`base - A·llm_fail - B·tool_fail + C·io_boost + aging`，夹在 0..4 | `test_band_change_inserts_at_tail_and_aging_caps` |
| 老化 | 仅 READY 且在队列中的任务累计 `wait_ms`；每 500ms +1，封顶 +2；RUNNING/BLOCKED/yielded 不计 | `test_ready_wait_ages_to_band_3_and_preempts_at_step_boundary`、`test_ready_wait_ignores_blocked_and_yielded` |
| I/O 完成加分 | 工具成功唤醒时先置 `io_boost_pending` 再入队；阻塞、量子用尽、更高带抢占、终态时清除；队头续跑不清 | `test_io_return_boosts_then_quantum_expiry_clears_it`、`test_no_io_boost_ablation_keeps_the_proc_in_band_2` |
| 失败惩罚 | 仅 `llm_parse` / `tool_fail` 发 `priority_decay`；数值不变也发；资源失败、权限拒绝不发 | `test_llm_parse_penalty_changes_band_and_tail_inserts`、`test_llm_resource_blocks_without_penalty`、`test_perm_deny_fails_without_penalty` |
| K=3 | 三次同类失败直接 FAILED，不发 `priority_decay`，不再入队；三种策略都执行 | `test_third_llm_parse_is_terminal_without_decay`、`test_fcfs_and_rr_have_no_priority_decay_but_still_stop_at_k` |
| 消融 | `enable_fail_penalty=False` 时 A/B 项为 0，但计数与 K 仍生效；`enable_io_boost=False` 时 C 项为 0；两者都关仍保留带与老化 | `tests/test_ablation.py`（6 项） |
| 混合负载 | [mix.py](../../agentos/workloads/mix.py)：16/12/8/4、固定 RNG 顺序、到达间隔 30ms | `tests/test_mix_determinism.py`（4 项） |
| 指标口径 | [metrics.py](../../agentos/experiments/metrics.py)：均值、p95、DONE 均值、wasted/retry、short-held、mismatch | `tests/test_metrics.py`（5 项） |
| 验收 3 | [demo_fail.py](../../agentos/workloads/demo_fail.py)：两个 short 加一个 flaky，`tool_forces={2,4,6}` 全失败 | `test_acceptance_3_fail_versus_fcfs` |

## 验收 3 实际输出

```text
$ python -m agentos.workloads.demo_fail
fifo: flaky state=FAILED exit=TOOL_FAIL decays=[]
fail: flaky state=FAILED exit=TOOL_FAIL decays=[(2, 1, 'tool_fail'), (1, 0, 'tool_fail')]
```

解释：

- flaky 的工具发出步是 2、4、6，三次都通过 `tool_forces` 失败，不消耗 `rng_search`。
- 失败感知策略在第 1、2 次失败后分别发出 `priority_decay`：`2→1`、`1→0`，cause 均为 `tool_fail`。
- 第 3 次失败达到 K=3，直接 `task_terminal state=FAILED exit_reason=TOOL_FAIL`，**不发** `priority_decay`，该 pid 不再入队。
- FCFS 轨迹零条 `priority_decay`，但同样在第 3 次失败后变成 FAILED，说明 K=3 是内核规则，惩罚只是策略层。
- 首次降权 `old=2,new=1` 时没有老化加分：两个 short 的服务时间加工具退避不足以让 flaky 在 READY 上等满 500ms。

## 固定种子冒烟（不是最终实验报告）

用 `build_mix(seed=20261103)` 跑 40 任务、同一种子的五种配置，虚拟时间，单次运行：

| 配置 | short 均值 | long 均值 | io 均值 | flaky 均值 | short-held | wasted | retry |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fifo | 11370 | 8937.5 | 19880 | 19735 | 135 | 0 | 480 |
| rr | 3315 | 18380.8 | 17175 | 14605 | 36 | 0 | 480 |
| fail | 5279.4 | 18114.2 | 12795 | 17492.5 | 50 | 0 | 480 |
| fail `--no-fail-penalty` | 5339.4 | 18600.8 | 13350 | 12382.5 | 52 | 0 | 480 |
| fail `--no-io-boost` | 5229.4 | 18074.2 | 13725 | 17545 | 49 | 0 | 480 |

说明：

- 所有配置 `context_mismatch == 0`，`clock=virtual`。
- 本混合负载没有 `LLM_PARSE`，所以 `wasted_llm_tokens` 为 0；`retry_llm_tokens=480` 来自 flaky 在工具失败后的下一次模型步（每步 96 token，共 5 步），这正是定义中“模型步开始时工具失败计数 > 0”的部分。
- 这是固定种子的一次冒烟输出，用来证明负载、消融和指标口径可跑通，不用于宣称哪条假设成立。正式报告应由 `experiments/run_sched.py` 生成 JSON 后再比较。

## 给 Role D 的接口

- `build_mix(seed=20261103, n=40) -> list[TaskSpec]`：任务已按到达顺序排好，`arrival_ms = i * 30`。
- `metrics_for_run(kernel, stats) -> dict`：包含 `by_class`（n / mean / p95 / done_n / done_mean）、`wasted_llm_tokens`、`retry_llm_tokens`、`retry_llm_ms`、`short_held_selections`、`context_mismatch`、`clock`。
- 轨迹 `syscall_exit` 现在带 `tokens`、`cpu_ms` 与两个 `consecutive_*` 计数；计数是 `apply` 之前的值，正好表示“该模型步开始时”的状态。
- `--policy fail`、`--no-fail-penalty`、`--no-io-boost` 分别对应 `Kernel(policy="fail")` 与 `Config(enable_fail_penalty=False, enable_io_boost=False)`。`--seed` 要同时传给 `build_mix` 和 `Config(EXPERIMENT_SEED=seed)`，后者供 `Kernel.rng_search` 使用。
- 报告时 `wasted_llm_tokens` 与 `retry_llm_tokens` 必须分列，禁止相加。

## 说明

- 关闭 `enable_fail_penalty` 后，惩罚路径仍可能发 `priority_decay`，但 `old == new`，因为公式里的 A/B 项为 0；这符合设计“数值没变也发”的约定。消融表里不能把它误读成“仍在降权”。
- 两个开关都关仍然有五个带、老化与带内量子轮转，**不是 RR**。
- 调度器不读取 `goal`、模型原文或 `observation_summary`；优先级只由 PCB 上的计数、等待时间和 I/O 标志决定。
