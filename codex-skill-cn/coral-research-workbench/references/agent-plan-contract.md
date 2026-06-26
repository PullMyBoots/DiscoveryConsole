# Agent 计划契约

在改变 agent 初始化或路线规划行为前使用本参考。

## 归属

Codex 拥有具体的 agent 初始化包。用户应该检查它，并在看起来不对时请求重新生成，但通常不应该在 dashboard 中手工编辑单个初始化计划或首次评估脚本。

## 路线计划

CORAL 让多个 agent 针对同一个共享 public 状态空间运行。多样性来自初始技术路线以及这些路线产生的评估反馈。

在以下情况使用多条路线：

- 存在多个合理的方法家族，
- 存在过早收敛风险，
- 评估具有有意义的组件指标，
- 或用户想要广泛的技术探索。

仍然要区分 agent。不要启动多个起始方法相同的 agent。

## Agent 初始化包

将可运行的 agent 初始化计划和首次评估脚本存放在：

```text
knowledge/briefs/agent-seeds/
```

每个 agent 必须有：

- `knowledge/briefs/agent-seeds/<agent-id>.md`
- `knowledge/briefs/agent-seeds/<agent-id>.eval.sh`

每个 Markdown 计划应包含：

- 标题
- 起始假设或技术方向
- 使用 `coral kb index ...` 的知识查询指令
- 首先尝试的可运行实现或诊断
- 首次评估脚本路径
- 要避免的事项
- 预期评估 profile
- 任何护栏顾虑
- agent 应如何根据评估反馈演化路线

计划要短到可以注入 agent 上下文，而不会变成一篇论文。它们是起始技术计划。

每个首次评估脚本必须可执行，并且必须提交正式 CORAL 评估，通常通过调用 `coral eval -m "<message>"`。它不应自行编辑代码。它的作用是给 agent 一条具体启动轨道：应用第一项路线特定改动，然后运行脚本获取分数证据。

## 知识访问

Agent 通过以下方式访问知识：

- `coral kb index manual`
- `coral kb index external`
- `coral kb index practice --by score|route|agent|metric`
- `coral kb read <id>`
- `coral kb note "..."`
- `coral kb archive --attempt <hash>`

如果某条路线需要大量背景资料，将该参考注册为 external knowledge，并在 agent 计划中引用具体的 `src-*` id。

## 重新生成

在以下情况于启动前重新生成计划：

- agent 重叠过多，
- 某条路线忽略了重要知识，
- 评估提示需要不同分解方式，
- 或用户拒绝拟定的搜索策略。

已有 attempt 后，改变路线就是新的实验条件。通过下一次恢复指令记录，或在改动较大时 fork 新 timestamp。
