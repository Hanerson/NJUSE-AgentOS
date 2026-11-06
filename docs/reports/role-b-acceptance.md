# Role B 系统调用验收记录

## 范围

Role B 按设计文档完成并验收以下边界：

- `context.py`：按 pid 保存/恢复深拷贝快照，工具完成只更新目标 pid；
- `perm.py` / `syscall.py`：参数校验后执行 Allow / Confirm / Deny，未知工具默认 Deny；
- `tool.py` / `events.py`：目录根约束，工具只登记虚拟完成事件，不推进时钟、不真实等待；
- `memory/store.py`：仅 `GOAL_DONE` 追加持久记录，`recall` 同步读取最近 20 条；
- `runtime.py`：每次只执行一次模型生成或一次挂起 syscall，覆盖正常完成、步数上限、超时、模型错误和资源退避。

## API Key 检查

角色 B 与三条核心验收使用 `ScriptedBackend`，没有网络请求，也不读取 API Key。仓库当前没有 `agentos/llm/http.py`；真实模型是角色 D 的可选 PR9，届时才从环境变量读取 `AGENTOS_LLM_BASE_URL`、`AGENTOS_LLM_API_KEY`、`AGENTOS_LLM_MODEL`。因此本角色开发、pytest 与答辩核心验收均不需要 API Key。

## 自动化验收

专用门禁：

```bash
python -m pytest -q \
  tests/test_context_switch.py \
  tests/test_syscall_perm.py \
  tests/test_runtime_step.py \
  tests/test_issue_clock.py \
  tests/test_tool_overlap.py \
  tests/test_snapshot_owner.py
```

重点断言：

1. 调用方篡改 step buffer 不会污染已保存快照，保存/恢复不改调度计数。
2. `read_local` 目录逃逸、`delete_data` 与未知工具以 `PERM_DENY` 终止，且无工具副作用、无失败降权。
3. `write_report` 在确认前没有文件；允许后先从 `confirm:{pid}` 切到 `tool:{pid}:{step}`，工具完成时才写文件；拒绝则彻底摘除等待状态。
4. 工具发起的 `cpu_ms=0`，只创建 `now + service_ms` 的事件；失败强制值按发起工具的 `step_index` 查询。
5. `recall` 最多返回最近 20 条；持久文件只记录 `GOAL_DONE`。
6. Runtime 在 K=3 次解析失败后终止，429/超时类资源错误只退避，不增加失败计数。

全量命令：

```bash
python -m pytest -q
```

