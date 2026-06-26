---
name: coral-research-workbench
description: 构建并运行由 Codex 主导、人在回路中的 CORAL 研究工作台。用于 Codex 需要把研究想法转成可运行的 CORAL 工作区，判断想法是否已准备好进入多 agent 搜索，准备知识、基线、评估 profile、可运行的 agent 初始化计划和评估脚本，指导 CORAL 运行前/运行中/运行后的用户交互，或把该流程封装为可复用 Codex skill 的场景。
---

# CORAL 研究工作台

使用此 skill，让 Codex 成为用户与 CORAL 之间的桥梁。用户负责高层判断、科学品味、风险承受度和最终接受决定。Codex 负责研究框定、工作区准备、实现、分析和运行纪律。CORAL 负责轻量运行时外壳：预备好的 agent 进程、worktree、评估提交、算力调度、知识 CLI 和 dashboard 渲染。

## 职责契约

1. 把 Codex 视为用户的研究操作员，而不是 YAML 助手。
2. 在整个任务中持续关注两个问题：
   - 这是不是一个真正值得优化的问题？
   - 当前研究路径是否可行、可信、可评估？
3. 在研究框架清晰到足以判断进展之前，不要进入 CORAL 迭代。
4. 让用户调节高层控制项；Codex 准备具体文件、评估、知识、基线、agent 路线和工作区一致性。
5. 把 CORAL 结果视为需要与用户一起审计的证据，而不是自动成立的科学结论。
6. 不要让用户充当 YAML 作者、grader 作者或工作区维护者。询问研究判断；具体机制由你自己实现。

在改变人类/Codex/CORAL 工作流，或判断用户请求是否应该启动 CORAL 之前，阅读 `references/interaction-protocol.md`。

## 不可跳过的关卡

在 Codex 已准备并且用户可以检查以下内容之前，不要启动 CORAL：

- 具体研究目标和预期产物
- 必要文献、可复用项目、工具、数据集和任务上下文
- 基线方法和基线表现
- 突破指标、护栏指标、反作弊与过拟合检查
- 一个与任务匹配的评估级别（L1、L2 或 L3）；它们是备选项，不是同一次运行的并行模式
- 一个越高越好的标量分数，用于 CORAL 调度
- 成本受控的评估 profile，至少包含一个快速迭代 profile
- 可运行的逐 agent 初始化包：差异化技术路线和可执行的首次评估脚本
- 一个记录评估版本/profile 和知识来源的有效 timestamp 工作区

如果缺少任何关卡，把它视为 Codex 的准备问题。只向用户询问继续所需的最小决策。

在 Codex 修改任何任务工作区、timestamp 工作区、评估脚本/规格、知识索引、基线产物、agent 初始化包或 dashboard 启动配置之后，必须运行工作区验证关卡，然后才能把改动视为完成：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/coral-research-workbench/scripts/validate_workspace.py" \
  --task-dir <task-dir> \
  --run-dir <timestamp>/.coral
```

在 timestamp 尚不存在时，只使用 `--task-dir`。timestamp 已准备好后，同时使用两个参数。如果验证失败，修复工作区并重新运行同一命令；不要从失败的工作区启动或恢复 CORAL。

## 双循环模型

把 CORAL 用作双循环研究系统：

- 内循环：CORAL agent 在冻结的 timestamp 内搜索、提交 attempt、接收评估分数、共享知识并提升标量分数。
- 外循环：用户和 Codex 审查分数是否可信、评估是否有效、知识是否应该提升，以及下一次运行应该继续、转向还是 fork 新 timestamp。

外循环控制科学可信度。绝不要把它简化成“最高分获胜”。

## 工作流

### 0. 接收并框定

先识别用户当前状态：

- 只有模糊方向，没有具体研究问题
- 方向清楚，但瓶颈未诊断
- 已诊断的方法或系统问题
- 成熟方法需要细化或验证

然后产出一个简短研究框架，包含目标、基线、所需证据、风险、评估计划和预期最终主张。当该框架具体到足以进行计分迭代时，再启动 CORAL 运行。

在把模糊或部分成形的想法转为可运行研究任务时，阅读 `references/research-framing.md`。

### 1. 检查 CORAL 可用性

准备工作区前，验证执行引擎已安装：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/coral-research-workbench/scripts/check_coral_install.py" --json
```

如果报告 `status: missing`，告诉用户必须先安装完整的 CORAL 工具才能启动，并使用脚本打印出的安装命令。不要假装 Codex skill 本身可以运行 CORAL；skill 是工作流适配器，而仓库/CLI 是执行引擎。

### 2. 准备知识和工作区

创建或更新 CORAL 任务目录：

```text
<task>/
├── task.yaml
├── seed/
├── grader/
├── knowledge/
└── results/
```

需要骨架时使用 `scripts/prepare_knowledge.py`。保持记忆系统以索引优先且规模较小：

- `knowledge/eval_spec.md`：计分契约和安全规则。
- `knowledge/manuals/`：简短框架手册。
- `knowledge/external/index.jsonl` 加 `knowledge/external/items/`：静态外部论文、仓库、文档、数据集和 Web 引用。
- `knowledge/practice/agents/`：与评估关联的 notebook、路线、分数曲线和反思。
- `knowledge/briefs/agent-seeds/`：Codex 准备的起始路线和首次评估脚本。

使用 `coral kb add external <url-or-path> --kind ... --title ... --summary ...` 添加每个外部来源。Agent 应该先使用 `coral kb index ...`，再使用 `coral kb read <id>`，而不是浏览知识文件系统。

在改变运行布局、知识路径、timestamp 行为或基线记录之前，阅读 `references/workspace-contract.md`。

创建或修改此工作区后，运行“不可跳过的关卡”中描述的验证关卡。即使改动看起来只是机械操作，这也是强制要求。

### 3. 在 Agent 运行前设计评估

把评估视为信任基础。它必须尽可能让“高分”意味着“确实更好”。

评估应包含：

- 衡量应改进内容的突破指标
- 衡量不得破坏内容的护栏指标
- 针对无效输出、泄漏、作弊、过拟合和格式违规的硬失败检查
- 成本受控的 profile，例如 `quick`、`medium`、`full` 和 `stress`
- 一个标量调度分数，同时保留具名组件指标供审查

在编写 grader 前，与用户明确选择一个评估级别：

- L1：固定/开放计分；agent 可以看到并调用计分函数。
- L2：开放 A 空间探索，隐藏 B 空间排名评估。
- L3：开放 A 空间加隐藏 B 空间迭代，并在常规 CORAL 循环外使用封闭 C 空间做最终验证。

对一个研究问题而言，L1/L2/L3 不是三个并行设置，也不是运行时可调旋钮。用户和 Codex 一旦确定“要研究什么”以及“结果要支撑什么主张”，这个问题理论上就应当按研究设计匹配一个评估级别。核心判据是目标环境的确定性：越固定、越封闭、越明确的场景，越靠近 L1；越开放、越不确定、越依赖真实部署环境的场景，越靠近 L3。

- L1：适合高度固定的场景，例如优化某个已知程序环节、kernel、脚本或 benchmark 组件。计分契约是开放的，真正目标就是在这个契约下直接改进。
- L2：适合场景仍然固定、评估体系足够成熟，但公开 probe 容易导致过拟合的问题。Agent 可以在 A-space 探索；隐藏的 B-space 负责排名或接受判断。
- L3：适合开放世界或部署环境不确定的主张。A/B 证据可以帮助搜索和筛选，但 B-space 上的 winner 仍可能只是验证体系上的局部最优或过拟合结果，因此 C-space 必须封闭，并用于常规 agent loop 之外的最终人类/Codex 验证。

如果研究主张包含泛化性，A/B/C 不能被当作同一分布里的随意随机切分。它们应当被设计成逐级增强的证据阶梯：A 要便宜、可学习、足以提供优化信号，但不能简单到和真实问题脱节；B 要更具代表性并保持隐藏，用来检验对 A 的过拟合；C 要最接近真实目标环境或最终科学主张。A 到 B、B 到 C 的差距应当是有意设计的，但不能断档。A 太简单或 B/C 距离太远，都会让 agent 无法优化，也会让最终证据难以解释。

不要把同一个研究问题做成“先试 L1，再试 L2，再试 L3”。如果后来发现评估级别判断错了，应把它视为研究设计改变：fork 新的 task version 或 timestamp lineage，记录新的 eval contract，并且不要把新旧分数当作同一实验直接比较。

把人类可读的信任论证写入 `knowledge/eval_spec.md`。如果已有 attempt 后评估含义发生变化，先启动新 timestamp，或在一个冻结评估下重新运行选定 attempt，再比较分数。

编写或编辑 grader 代码后，运行验证关卡。`coral validate <task-dir>` 必须加载 grader 入口点并在 `seed/` 上执行它。未能通过 `TaskGrader.report_score(...)` / `fail_report(...)` 返回标量、结构化指标或 `eval_report`，应视为评估契约 bug，而不是 agent 问题。

在设计评估哲学时阅读 `references/eval-design.md`，在编写或修改 grader 前阅读 `references/eval-contract.md`。

### 4. 生成可运行的 Agent 初始化包

Codex 负责启动计划。不要要求用户在普通控制面板中手动添加/删除 agent 或编辑逐 agent 内部细节。

为每个 agent 写入可运行初始化包：

- `knowledge/briefs/agent-seeds/` 下的一份不同初始化计划
- `knowledge/briefs/agent-seeds/<agent-id>.eval.sh` 下的一份可执行首次评估脚本
- 一个具体的首次实现或诊断步骤
- 第一步后要运行的精确评估命令/脚本，以及要观察的信号
- 应避免的事项，包括护栏和过拟合风险
- 演化规则：从此计划开始，然后只根据评估反馈和共享证据修订路线

Codex 选定具体路线后，使用 `scripts/prepare_agent_plan.py`。只有这份计划存在后，用户才应该调节运行时/模型/资源控制项。

在改变 agent 初始化或路线规划行为前，阅读 `references/agent-plan-contract.md`。

### 5. 物化 Timestamp 工作区

在人类启动 agent 前，Codex 必须把任务目录转成具体的 timestamp 运行：

```bash
coral prepare -c <task-dir>/task.yaml
```

这会创建冻结的运行目录、仓库 clone、共享 `.coral/public` 状态，以及每个已规划 agent 的隔离 worktree。完成后验证准备好的 timestamp：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/coral-research-workbench/scripts/validate_workspace.py" \
  --task-dir <task-dir> \
  --run-dir <timestamp>/.coral
```

启动前修复所有缺失的 readiness 项。有效启动命令指向已准备运行配置，而不是原始任务配置：

```bash
coral start -c <timestamp>/.coral/config.yaml
```

### 6. 只暴露对用户安全的控制项

控制面板应暴露高层控制项：

- executor/backend、model 和 reasoning effort
- 通过 `run.max_runtime_seconds` 设置总运行时长
- 评估 profile
- 网络权限
- evaluator 总资源预算
- 暂停/恢复和下一次恢复指令

不要把原始 YAML、grader 内部细节、逐 agent 初始化脚本、分数方向或低层资源字段作为普通用户旋钮暴露。

在判断哪些内容属于 UI 前，阅读 `references/control-panel-boundaries.md`。

### 7. 启动并监督

只有满足以下条件后才启动 CORAL：

- `scripts/validate_workspace.py --task-dir <task-dir> --run-dir <timestamp>/.coral` 通过
- seed/baseline 产出已记录分数
- 知识索引和评估规格存在
- 评估版本/profile 已记录
- agent 路线已准备好

运行期间，让用户观察进展、暂停/恢复整个运行，并可选发送下一次恢复指令。如果用户在暂停或停止期间给出反馈，将运行级 steering 保存到 `.coral/public/control/next_instruction.md`。对于定向的逐 agent 批评，用 `coral kb notebook --agent <agent-id> --set <file> --reason external-adjustment --by codex` 重置该 agent 的 notebook。只通过 `coral kb add external ...` 和 `coral kb remove <src-id>` 添加或归档外部知识。已有 attempt 后，不要静默重写评估语义、隐藏数据或 agent 初始化包。

一旦 timestamp 有活动，就锁定 evaluation level、executor/runtime backend、grader direction、eval version 和 route topology。在安全时，保持 model、eval profile、resource budget、deadline 和 next-resume instruction 可编辑。

### 8. 审查、提升或 Fork

运行停止后，与用户一起审查：

- 最佳 attempt 和失败模式
- 基线差值和分数组件行为
- 评估可靠性、过拟合风险和 reward hacking 风险
- 值得保持 active 的外部来源
- 值得为下一个 timestamp 保留的 practice knowledge
- 是否继续、转向、改变评估或 fork 新 timestamp

把 `/api/review` 或 Knowledge dashboard 的 Review 面板作为第一证据界面。如果评估含义发生变化，启动新 timestamp，并在产生新 attempt 前记录新的评估版本。

在做运行后主张、提升知识或 fork timestamp 前，阅读 `references/run-review-protocol.md`。
