# AgentOS

NJUSE 课程期末大作业：以系统调用为粒度、带失败感知的 Agent 调度内核。内核是单线程的协作式调度器：一次 `Kernel.step` 只推进一个 LLM 生成或一个挂起的系统调用，工具等待通过显式事件完成，虚拟时钟不会被真实 `sleep` 阻塞。

## 项目结构

```text
NJUSE-AgentOS/
├── agentos/                    # 内核与用户态脚本
│   ├── kernel/                 # 进程表、虚拟时钟、事件、主循环
│   │   ├── pcb.py              # PCB、ProcState、合法状态迁移
│   │   ├── clock.py            # VirtualClock
│   │   ├── events.py           # 最小堆事件队列
│   │   ├── loop.py             # 主循环：dispatch / flush / pick / apply
│   │   ├── runtime.py          # 一步运行时
│   │   ├── context.py          # 上下文快照 save/restore
│   │   ├── syscall.py          # 系统调用分发
│   │   ├── perm.py / tool.py   # 权限与工具
│   │   ├── trace.py            # JSONL 轨迹
│   │   └── sched/              # 调度策略
│   │       ├── base.py         # 调度器基类与优先级公式
│   │       ├── fifo.py         # FCFS
│   │       └── rr.py           # Round-Robin（token 量子）
│   ├── llm/                    # 模型后端（脚本后端）
│   ├── memory/                 # 持久记录
│   └── workloads/              # 演示负载
├── tests/                      # pytest 测试
├── docs/                       # 设计文档与参考资料
│   ├── design/                 # 内核设计文档
│   ├── reference/              # 作业说明、分工表
│   └── reports/                # 验收记录
├── pyproject.toml
└── README.md
```

## 环境与运行

需要 Python 3.11+。

```bash
# 安装开发依赖（可选，也可以直接使用已安装的 pytest）
pip install -e ".[dev]"

# 运行全部测试
python -m pytest -q

# 验收 1：RR 抢占后上下文恢复（t=160 队列为 [S, L]，L 从第 4 步继续）
python -m agentos.workloads.demo_preempt

# 验收 2：工具等待期间另一个任务占用模型（FCFS）
python -m agentos.workloads.demo_overlap
```

演示命令会在当前目录生成运行时数据 `var/`，该目录不纳入版本控制。

## 持续集成

`.github/workflows/ci.yml` 在 push / PR 时运行：

- 全量测试矩阵：Ubuntu（Python 3.11/3.12/3.13）与 Windows（Python 3.12）
- **Role A acceptance gate**：独立运行 PCB、虚拟时钟、主循环、FCFS、RR 的测试与验收演示
- **Role B acceptance gate**：独立运行上下文、系统调用、权限、确认、工具与一步运行时测试
- 构建 sdist/wheel 并上传 artifact

打 `v*` tag 时 `release.yml` 会重新测试、构建并创建 GitHub Release。详见 [CI/CD 说明](docs/ci.md)。

## 文档

- [内核设计文档](docs/design/AgentOS-调度内核设计.md)
- [课程作业说明（PDF）](docs/reference/L10-期末大作业agentos.pdf)
- [四人分工表](docs/reference/ref.png)
- [Role A 验收记录](docs/reports/role-a-acceptance.md)
- [Role B 验收记录](docs/reports/role-b-acceptance.md)
- [CI/CD 说明](docs/ci.md)

## 开发分工

| 角色 | 职责 | 主要文件 | 状态 |
| --- | --- | --- | --- |
| A 内核 | PCB、虚拟时钟、主循环、FCFS、RR | `pcb.py`、`clock.py`、`loop.py`、`sched/fifo.py`、`sched/rr.py` | 已完成，验收 1 通过 |
| B 系统调用 | 上下文、权限、工具、确认 | `context.py`、`syscall.py`、`perm.py`、`tool.py`、`runtime.py` | 已完成；上下文、权限、工具、确认与 Runtime 门禁通过 |
| C 策略与实验 | 失败感知优先级、消融、指标口径 | `sched/fail_priority.py`、`workloads/mix.py` | 未开始（`base.py` 已预留优先级带接口） |
| D 轨迹与演示 | 事件日志、实验驱动、HTTP 桩、讲稿 | `trace.py`、`experiments/run_sched.py`、`llm/http.py`、`demo_*.py` | `trace.py` 与 `demo_overlap/preempt` 已具备；实验驱动与 HTTP 后端待补 |
