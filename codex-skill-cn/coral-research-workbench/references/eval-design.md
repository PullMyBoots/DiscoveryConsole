# 评估设计

在为开放式研究搜索设计评估哲学时使用本参考。

## 核心原则

标量分数不只是一个数字。它是说明某个发现结果更好的论证。CORAL 的调度器优化这个标量，因此这个标量必须被设计成能抵抗误导性进展。

## 指标组

使用两类指标组加硬失败：

- 突破指标：量化应改进的内容。通常越高越好。
- 护栏指标：量化必须保持可用、有效、稳健或高效的内容。这些通常有最低可接受下限。
- 硬失败：让作弊、数据泄漏、破坏输出格式、超时或利用 grader 漏洞的 attempt 无效。

然后定义一个标量：

```text
score = f(breakthrough_metrics, guardrail_metrics, hard_failures)
```

优先采用越高越好。把公式记录在 `knowledge/eval_spec.md` 中。

## 护栏不是可选项

如果结果存在以下问题，高突破分数并不可信：

- 违反输出契约，
- 在重要 case 上崩溃，
- 使用隐藏标签或测试泄漏，
- 硬编码 benchmark artifact，
- 消耗不可接受的资源，
- 依赖不可复现环境，
- 或只是因为 quick eval 太窄而获胜。

将这些编码为硬失败或强惩罚。

## 过拟合和作弊

过拟合是可信度威胁，并不总是道德违规。把它作为反作弊和信任设计的一部分处理。

使用以下一种或多种方法：

- held-out case
- 随机种子
- stress test
- 分布偏移检查
- 隐藏或重新生成的 case
- 不变量检查
- 针对可疑捷径的消融
- 在 full profile 下重新评估

如果某类方法是确定性、解析性或受约束到不太可能过拟合，记录原因。不要忽略这个问题。

## 评估 Profile

有用时使用多个成本层级：

- `quick`：足够便宜，可频繁迭代；应能预测 full eval 的排序。
- `medium`：为有希望的 attempt 提供更强信号。
- `full`：主 benchmark 下的最终验证。
- `stress`：稳健性、边界 case、反作弊、分布偏移。

Agent 通常在 `quick` 下优化；用户和 Codex 用更强 profile 验证主张。

## 可比性

不要直接比较：

- 使用不同 eval version 计分的 attempt，
- 像测量同一件事一样比较 `quick` 和 `full` 分数，
- 改变 grader 语义前后的 attempt，
- 使用不同隐藏数据或不同资源契约的 attempt，除非已记录并规范化。

如果评估含义变化，递增 `grader.eval_version` 并启动新 timestamp，或在一个冻结评估下重新运行选定 attempt。

## Eval Spec 检查表

`knowledge/eval_spec.md` 应说明：

- 标量分数意味着什么，
- 所有突破指标，
- 所有护栏指标和下限，
- 硬失败条件，
- agent 可能利用评估的已知方式，
- quick/full/stress profile 的差异，
- 什么证据足以接受一个结果，
- 什么证据会要求再次运行。
