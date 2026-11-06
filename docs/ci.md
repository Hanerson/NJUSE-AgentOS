# CI/CD 说明

工作流位于 `.github/workflows/`，使用 GitHub Actions。目标是：后续角色继续提交时，Role A 的调度内核行为与 Role B 的系统调用边界一旦被破坏，PR 不能合并。

## 触发方式

| 工作流 | 触发 | 内容 |
| --- | --- | --- |
| [CI](../.github/workflows/ci.yml) | push 到 `main`、任意 PR、手动触发 | 测试矩阵、Role A/B 门禁、构建发行包 |
| [Release](../.github/workflows/release.yml) | push `v*` tag | 完整测试、构建、创建 GitHub Release 并附上 sdist/wheel |

## CI 的四个 Job

### 1. `tests (os, py版本)`

矩阵：

- Ubuntu × Python 3.11 / 3.12 / 3.13
- Windows × Python 3.12（本项目在 Windows 上开发）

每个组合执行 `python -m pytest -q`，覆盖全仓库测试。

### 2. `Role A acceptance gate`

Role A 的专用门禁，只跑归属 Role A / PR5 的测试：

```text
tests/test_pcb_states.py       PCB 状态机、虚拟时钟、常数
tests/test_issue_clock.py      工具发起不推进时钟
tests/test_no_sleep.py         AST 扫描：内核不得真实 sleep
tests/test_fifo_convoy.py      FCFS 不按步轮转
tests/test_tool_overlap.py     验收 2：工具等待期间 B 占用模型
tests/test_snapshot_owner.py   阻塞期间快照归属
tests/test_rr_quantum.py       验收 1：t=160 队列 [S, L]，L 从第 4 步继续
```

随后运行两个答辩演示入口，并断言输出：

- `demo_preempt`：`t=160 queue=[2, 1]`、L `DONE` 且 `step >= 4`、S `DONE`
- `demo_overlap`：`A=DONE B=DONE tool_done.ts_ms=540`

演示子进程有 10 秒超时，配合 `test_no_sleep` 防止有人用 `time.sleep` 伪造虚拟时间。

### 3. `Role B acceptance gate`

Role B 的专用门禁覆盖：

```text
tests/test_context_switch.py   快照深拷贝、pid 所有权、软上限告警
tests/test_syscall_perm.py     Allow/Confirm/Deny、路径约束、工具事件、recall
tests/test_runtime_step.py     单步 Runtime、终止分类、资源退避、K=3
tests/test_issue_clock.py      工具发起不推进虚拟时钟
tests/test_tool_overlap.py     工具阻塞期间模型可被其他任务使用
tests/test_snapshot_owner.py   工具完成只写被阻塞 pid 的快照
```

### 4. `Build distributions`

在 `test`、`role-a-gate` 与 `role-b-gate` 都通过后，构建 sdist 和 wheel，安装 wheel 做导入冒烟测试，并上传 `dist` artifact。

## 让门禁真正生效（仓库推送后手动做一次）

1. 推送到 GitHub 后进入 **Settings → Branches → Add branch protection rule**。
2. 分支填 `main`，勾选 **Require status checks to pass before merging**。
3. 把以下检查设为 required：
   - `Role A acceptance gate`
   - `Role B acceptance gate`
   - `tests (ubuntu-latest, py3.11)`
   - `tests (ubuntu-latest, py3.12)`
   - `tests (ubuntu-latest, py3.13)`
   - `tests (windows-latest, py3.12)`
   - `Build distributions`
4. 建议同时勾选 **Require branches to be up to date before merging**。

PR 模板 `.github/pull_request_template.md` 会提醒提交者：不得为了变绿而修改既有验收断言。

## 本地等价命令

提交前在仓库根目录执行：

```bash
python -m pytest -q
```

只跑 Role A 门禁：

```bash
python -m pytest -q \
  tests/test_pcb_states.py \
  tests/test_issue_clock.py \
  tests/test_no_sleep.py \
  tests/test_fifo_convoy.py \
  tests/test_tool_overlap.py \
  tests/test_snapshot_owner.py \
  tests/test_rr_quantum.py

python -m agentos.workloads.demo_preempt
python -m agentos.workloads.demo_overlap
```

只跑 Role B 门禁：

```bash
python -m pytest -q \
  tests/test_context_switch.py \
  tests/test_syscall_perm.py \
  tests/test_runtime_step.py \
  tests/test_issue_clock.py \
  tests/test_tool_overlap.py \
  tests/test_snapshot_owner.py
```

## 发布流程（CD）

版本号当前在 `pyproject.toml` 的 `version`。发布时：

```bash
# 1. 修改 pyproject.toml 的 version，例如 0.2.0
# 2. 提交并推送 main，等待 CI 全绿
git tag v0.2.0
git push origin v0.2.0
```

tag 推送后 `Release` 工作流会重新跑测试、构建 `dist/*.whl` 与 `dist/*.tar.gz`，并用 `gh release create` 创建 Release。没有配置 PyPI 发布；需要时再加 trusted publishing。

## 后续角色接入建议

- **B（系统调用）**：已完成，后续改动必须同时保持 `Role B acceptance gate` 全绿。
- **C（策略）**：新增 `sched/fail_priority.py` 时，确保 `base.py` 的 `uses_bands` 路径不改变 FCFS/RR 行为；`test_fifo_convoy.py` 与 `test_rr_quantum.py` 必须保持绿。
- **D（实验驱动）**：新增 `experiments/run_sched.py`、`llm/http.py` 时，不要把 `time.sleep` 带进 `agentos/kernel/`、`agentos/llm/scripted.py`、`agentos/workloads/`，`test_no_sleep.py` 会扫描 AST。
- 如果新增 Python 版本，只改 `ci.yml` 的矩阵即可。
