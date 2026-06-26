# 研究框定

在从模糊或部分指定的研究想法准备 CORAL 工作区前使用本参考。

## 输出

启动前产出一个简短研究框架：

```text
Objective:
Expected artifact:
Current baseline:
Known bottleneck:
Breakthrough target:
Guardrails:
Anti-cheating / overfitting risks:
Eval profiles:
Required knowledge:
Agent route strategy:
Evidence needed for a claim:
```

这个框架不是论文 proposal。它是让 CORAL 搜索有意义所需的最小结构。

## Readiness 问题

提出并回答：

1. 具体要改进什么？
2. 什么绝不能变差？
3. 哪个基线能证明任务不是平凡的？
4. 哪种失败会让高分不可信？
5. agent 启动前必须看到哪些数据、仓库、论文、工具或领域上下文？
6. 如果运行成功，用户最终想提出什么主张？
7. 在最佳 quick-eval attempt 之后还需要什么证据？

如果答案薄弱，继续框定。不要启动 CORAL。

## 基线标准

启动前，Codex 应该：

- 复现一个既有基线，
- 实现一个简单 seed baseline，
- 导入一个已知参考实现，
- 或清楚记录为什么不存在基线，以及将使用什么 proxy。

在 timestamp 中记录 baseline attempt，使 dashboard 比较有可见参考线。

## 知识标准

启动知识应包含 agent 避免重新发现基础内容所需的信息：

- 相关论文和方法摘要
- 可复用开源项目
- 数据集或 benchmark 文档
- 已知约束和失败模式
- 工具链设置备注
- 用户既有笔记和偏好

用 `coral kb add external` 注册这些内容，使其出现在 `knowledge/external/index.jsonl` 中。用 `coral kb note` 或 `coral kb archive --attempt <hash>` 将运行时结论保存在 practice knowledge 中。

## Agent 路线标准

Agent 路线应有实质差异，而不是同一计划的随机改写。

好的差异化轴包括：

- 保守改进 vs 高风险重设计
- 理论驱动 vs 经验搜索
- 速度/延迟优化 vs 准确性优化
- 护栏优先验证 vs 突破优先探索
- 不同算法家族
- 不同数据表示或损失函数

Agent 路线应分离技术方法家族、证据目标或诊断角度，同时仍共享同一份 public run knowledge。

## 何时停止框定

满足以下条件时，转入工作区准备：

- 问题能表达为 task spec，
- 至少一个基线可以被计分，
- 评估有合理的信任论证，
- 必要知识可以被索引，
- agent 路线可以被区分，
- 并且用户理解第一次 CORAL 运行打算检验什么。
