# 运行审查协议

在 CORAL 运行停止或暂停后，当用户想理解发生了什么或决定下一步时使用本参考。

## 目的

审查循环把原始 CORAL 活动转化为科学判断。目标不是庆祝最高分，而是判断结果是否可信、有用、值得提升。

## 要检查的证据

从 `/api/review` 或 Knowledge dashboard 的 Review 面板开始。然后检查：

- 最佳 attempt 及其分数组件
- 基线差值
- 失败和待处理的 eval job
- eval version/profile 身份
- `eval_spec.md` 的变化
- 新笔记和来源
- dashboard 摘要不足时才检查 agent 日志
- 相关时检查资源和成本信号

## 审查问题

询问：

1. 最佳 attempt 是否改进了用户关心的内容？
2. 它是否保持了护栏？
3. 该分数是否可能来自 reward hacking、泄漏、过拟合或 benchmark 运气？
4. 多个 agent 是否独立收敛到该结果，还是它只是孤例？
5. quick-eval 排名是否可能经受 full/stress eval？
6. 是否有新来源或笔记改变了研究框定？
7. 下一步应该继续、转向、验证、重写评估还是停止？

## 知识提升

将外部来源分类为 active 或 archived。用 `coral kb add external` 保持有用参考 active；用 `coral kb remove` 归档过时参考。

持久结论应通过 `coral kb note` 或 `coral kb archive --attempt <hash>` 变成 practice knowledge。

## 恢复指令

当用户想引导同一 timestamp 的下一次继续时，将指令保存到：

```text
.coral/public/control/next_instruction.md
```

好的恢复指令应简洁且可操作：

- 强调什么，
- 停止做什么，
- 检查什么证据，
- 复用什么来源或 attempt，
- 保护什么护栏。

不要用恢复指令隐藏评估改变。如果计分含义改变，就 fork。

## 定向调整

使用与用户反馈相匹配的最小调整：

- 运行级 steering：写入 `.coral/public/control/next_instruction.md`。
- Agent 级批评：用 `coral kb notebook --agent <agent-id> --set <file> --reason external-adjustment --by codex` 重置该 agent 的 notebook。
- 外部知识变更：使用 `coral kb add external ...` 或 `coral kb remove <src-id>`。
- 评估含义变化、重大路线重写或基线变化：fork 新 timestamp，而不是改变当前证据。

## Fork 新 Timestamp

在以下情况 fork：

- 评估语义改变，
- 重要知识已被提升，
- agent 计划发生实质变化，
- 基线改变，
- 或旧 attempt 不应再在新设置下解释。

新 timestamp 应按需复制 config、snapshot、已接受知识和准备好的路线，但不应把旧 attempt 当作在新条件下计分的结果复制过来。

## 接受

只有满足以下条件时，结果才可以作为主张呈现：

- 在相关评估下超过基线，
- 护栏通过，
- 已处理反作弊和过拟合风险，
- eval identity 和 profile 已记录，
- 结果可以复现，
- 并且用户同意证据支持预期主张。

否则，将其描述为有希望的候选，而不是已经解决的研究结果。
