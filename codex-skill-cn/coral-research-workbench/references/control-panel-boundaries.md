# 控制面板边界

在判断哪些 CORAL 字段属于用户面板时使用本参考。

## 面向用户的控制项

将这些内容展示为简单控制项：

- 任务名称和工作区路径：只读
- executor：Codex、Claude Code、OpenCode 等
- model
- 通过 `agents.runtime_options.model_reasoning_effort` 设置 reasoning effort
- 通过 `run.max_runtime_seconds` 设置总运行时间/截止时间
- 网络权限
- 评估 profile
- 通过 `grader.parallel.resources` 设置 evaluator 总 CPU/GPU/内存预算
- 分数图表指标、排序和范围控制

## Codex 拥有的设置

不要把这些内容作为普通面板编辑项暴露：

- 工作区生成后的精确 agent 数量
- 作为普通运行预算控制项的 `agents.max_turns`
- 逐 agent 初始技术方向
- 逐 agent 首次评估脚本
- grader 入口点
- grader direction（`maximize` / `minimize`）
- 原始 setup 命令
- 每次评估的资源需求（`grader.resources` 和 profile 资源覆盖）
- 私有 grader 文件
- 基线实现细节
- 知识文件放置位置

把这些内容显示为只读计划预览。Agent Plan 从 `.coral/public/knowledge/briefs/agent-seeds/*.md` 读取初始化包。如果用户不喜欢该计划，Codex 应在启动前重新生成工作区/计划。

Knowledge 面板可以让用户记录审查笔记和拟添加来源，并将 run-global manifest 引用标记为 accepted/rejected/archived。它不能静默删除源文件或改变源知识。
它也可以把 `.coral/public/knowledge/eval_spec.md` 作为 Markdown 编辑器暴露出来，让用户/Codex 在新 timestamp 运行前审查或修订信任论证。保存此文件不应改变既有 attempt，也不应静默改变它们记录的 eval version/profile。

Overview 图表可以暴露：

- 从总分或 `metadata.score_components` 中选择指标
- `Time` 顺序用于展示按时间的进展和 running-best
- `Score` 顺序用于展示按分数排序的排名分布
- 范围预设和显式的 1-based 范围输入

Overview agent 卡片应暴露：

- 可见状态：`work_loop`、`reflect_loop`、`waiting eval`、`paused` 或 `stopped`
- `evaluating` 表示 grader 正在运行该 attempt
- `waiting` 表示该 attempt 排在另一个 grader job 后面
- 可用时显示 active duration、距上次输出时间和状态持续时间
- 属于该 agent 的当前排队/运行中的 eval job 及其进度
- 最近几个 attempt 的状态、commit、title 和当前选择的分数指标

## 启动后锁定

agent 运行后，普通 UI 不应改变：

- agent 数量
- 既有 agent 的 executor/runtime backend
- worktree 位置
- 用于解释分数的 grader direction
- 既有 attempt 使用的 eval version

在 API 中强制这些锁定，而不仅是在前端禁用。控制面板保存路径应保留现有 `config.yaml` 中由 Codex 拥有的字段。同样，Readiness 为 `missing` 时，应在 API 中阻止 Run/Resume；不要只依赖禁用的浏览器按钮。

允许安全变更：

- 暂停/恢复/停止
- 延长或缩短截止时间
- 在明确记录时改变未来的评估 profile、worker 数量和资源预算；这些只应用于未来的 eval wave，不影响已经运行中的 job
- 通过 `.coral/public/control/next_instruction.md` 添加下一次恢复的用户备注或指令
- 作为高级操作停止或恢复单个 agent
