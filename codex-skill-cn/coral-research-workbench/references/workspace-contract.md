# 工作区契约

在创建或改变 CORAL 任务/运行布局时使用本参考。

## 任务目录

```text
<task>/
├── task.yaml
├── seed/
├── grader/
├── knowledge/
└── results/
```

- `task.yaml`：Codex 准备的源配置。
- `seed/`：初始代码/基础项目。Agent 不直接编辑它。
- `grader/`：版本化评估包。
- `knowledge/`：运行前准备的任务级知识。停止后将已审查的运行知识提升回这里。
- `results/`：生成的运行。

## Timestamp 运行

```text
results/<task-slug>/<timestamp>/
├── snapshots/
│   ├── task.yaml
│   ├── seed/
│   ├── grader/
│   └── knowledge/
├── repo/
├── agents/
└── .coral/
    ├── config.yaml
    ├── public/
    │   ├── knowledge/
    │   ├── attempts/
    │   ├── skills/
    │   ├── agents/
    │   ├── logs/
    │   ├── control/
    │   └── eval_logs/
    └── private/
```

每个 timestamp 必须在不依赖可变外部任务文件的情况下可解释。如果大型数据集/仓库让这件事成本过高，则存储带有不可变路径、校验和、commit 或对象存储 ID 的 manifest。

Codex 创建或修改任务工作区、timestamp 工作区、评估脚本/规格、知识索引、基线产物或 agent 初始化包之后，必须运行 skill 验证关卡：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/coral-research-workbench/scripts/validate_workspace.py" \
  --task-dir <task-dir> \
  --run-dir <timestamp>/.coral
```

在 timestamp 尚不存在时只使用 `--task-dir`。该脚本封装 `coral validate <task-dir>`，用于 dry-run 任务 grader；并封装 `coral validate --run-dir <timestamp>/.coral`，用于检查冻结 timestamp readiness。后者使用与 `/api/control/readiness` 相同的检查，并应在缺少 Codex 准备的 artifact 时失败。

基线程序或方法被评估后，在启动前将其分数记录为 timestamp attempt：

```bash
python scripts/record_baseline_attempt.py results/<task>/<timestamp>/.coral \
  --score <scalar-score> \
  --name seed
```

该脚本写入 `.coral/public/attempts/baseline-<name>.json`，其中包含 `metadata.baseline: true`、`metadata.reference: baseline` 和冻结的 eval version/profile。这是 Readiness 和 Overview 基线线条期望的基线 artifact。Readiness 和 Review 从 `.coral/public/attempts/` 读取基线。

## 知识布局

```text
knowledge/
├── index.md
├── eval_spec.md
├── manuals/
├── external/
│   ├── index.jsonl
│   └── items/
├── practice/
│   └── agents/
├── briefs/
│   └── agent-seeds/
```

`external/index.jsonl` 是外部来源注册表。使用 `coral kb add external <url-or-path> --kind paper|repo|web|doc|dataset --title "..." --summary "..."` 添加来源。CORAL 将来源记录存放在 `external/items/` 下。

`practice/agents/` 存储运行时经验：notebook、与评估关联的 chain node、分数曲线、路线摘要和反思。Agent 应通过 `coral kb index practice --by score|route|agent|metric` 和 `coral kb read <id>` 读取它，而不是浏览目录。

准备好的 agent worktree 将可复用框架说明暴露为根级 symlink：

```text
CORAL_OVERVIEW.md -> <shared-dir>/knowledge/manuals/coral-overview-cli.md
CORAL_LOOPS.md    -> <shared-dir>/knowledge/manuals/agent-loops.md
```

`CLAUDE.md` 和 `AGENTS.md` 应指向这些文件作为信息地图；可复用内容属于共享 manuals。

`eval_spec.md` 是 Codex 在启动前准备的计分信任论证。它应覆盖突破指标、护栏指标、反作弊和过拟合检查、标量分数公式，以及每个 eval profile 的目的。控制面板 Readiness 检查表需要这个文件。

外部来源使用 `active` 或 `archived` 状态。用 `coral kb remove <src-id>` 或 dashboard source action 归档来源。

Dashboard Knowledge 视图读取 `external/index.jsonl` 和 practice index。

Dashboard Review 面板由 `/api/review` 支撑。它汇总最佳 attempt、基线差值、eval identity、失败/待处理 eval、知识计数、readiness 和建议审查动作。通过 `coral kb note` 或 `coral kb archive --attempt <hash>` 持久化结论。

Dashboard 可以通过 `/api/knowledge/sources/status` 更新 manifest source 状态。它不会删除文件系统 source 文件，也不会重写 source knowledge。

当 dashboard 从已停止运行创建新 timestamp 时，CORAL 应通过 index-first 知识模型提升 active external source 和选定的 practice summary。源 timestamp 保持不变。
当当前 manager 或任何记录的 agent 进程仍存活时，dashboard 和 API 应阻止此操作；fork 前先暂停或停止运行。

## Agent 初始化包

Codex 拥有启动包。将差异化、可运行的初始化计划和首次评估脚本存放在这里：

```text
knowledge/briefs/
└── agent-seeds/
    ├── agent-1.md
    ├── agent-1.eval.sh
    ├── agent-2.md
    ├── agent-2.eval.sh
    └── agent-3.md
```

控制面板 Agent Plan preview 通过 `/api/control/plan` 读取这些文件。Readiness 要求 agent 初始化计划数量足够匹配配置的 agent 数量。

计划文件应以 `#` 标题开头，然后包含简洁技术路线、可运行第一步、首次 eval 脚本路径、avoid list 和 evolution rule。Eval 脚本必须可执行并提交正式 `coral eval`。这些是起始技术包。
不要让用户在控制面板中编辑它们；如果计划不好，用 Codex 重新生成工作区计划。

使用 `scripts/prepare_agent_plan.py` 物化这些文件。Codex 可以先写入带有 `agents` 的 JSON 计划，然后运行：

```bash
python scripts/prepare_agent_plan.py knowledge --plan plan.json --force
```

初始 placeholder 可使用：

```bash
python scripts/prepare_agent_plan.py knowledge --agents 4
```

## 控制备注

`.coral/public/control/next_instruction.md` 存储用户反馈或 steering note，在下一次恢复时注入。它是 run-scoped：保持在 timestamp 内，使该指令只应用于这个实验现场。
