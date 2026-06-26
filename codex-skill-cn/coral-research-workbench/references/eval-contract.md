# 评估契约

在为研究搜索编写或改变 CORAL grader 前使用本参考。

## 必需属性

1. grader 必须返回一个用于 CORAL 调度的标量分数。
2. 多维指标必须作为 `ScoreBundle.scores` 返回；CORAL 会把它们记录在 attempt 的 `metadata.score_components` 下。
3. grader 应通过 `TaskGrader.report_score(...)` 或 `TaskGrader.fail_report(...)` 返回 `metadata.eval_report`。CORAL 会在 attempt finalized 后，用 rank、top-5、self-history 和 baselines 增强此 report。
4. 标量分数必须组合：
   - 突破指标：应改进的内容
   - 护栏指标：不得破坏的内容
   - 硬失败检查：作弊、无效输出、超时、数据泄漏、格式违规
5. 每个 attempt 都必须记录 eval version、profile、evaluation level 和 evaluation space。

## 评估级别

编写 grader 前，与用户选择一个级别。选择取决于任务和预期主张；对固定任务而言，L1/L2/L3 是备选项，不是三个同时存在的模式。

- L1：固定/开放场景。A-space 计分机制对 agent 公开。用于目标是直接针对已知目标进行程序优化的任务。
- L2：带隐藏排名的开放探索。Agent 可以探测 A-space，但正式排名使用 B-space，以降低对公开 probe 的过拟合。
- L3：严格研究验证。Agent 使用 A/B 迭代，而 C-space 被封闭，用于 CORAL 运行后的人类/Codex 最终验证。CORAL 存储 C-space 资产，但常规 agent eval loop 不应暴露或运行它们。

所选级别应写入 `knowledge/eval_spec.md`，并说明该任务的允许 agent API 和隐藏边界。

启动前，将人类可读的信任论证写入 `knowledge/eval_spec.md`。它必须覆盖：

- agent API：agent 可以使用哪些命令/文件（`coral eval`、可选 `coral eval --tune`、`coral run -- <command>`，以及任何其他 public A-space exploration API）
- evaluation level：L1/L2/L3，以及 A/B/C spaces 对该任务意味着什么
- public metric names、directions 和安全解释
- acceptance criteria：最低分、必需测试、runtime/memory 限制、泄漏检查或其他硬关卡
- anti-cheating and overfitting checks：泄漏、无效输出、held-out 或 stress case，以及稳健性检查
- profile intent：quick/medium/full/stress 必须使用同一计分机制；较小 profile 只能在样本量、seed、case 或运行次数上不同
- feedback report：agent 在成功/失败时看到什么，以及哪些细节必须保持隐藏

控制面板 Readiness 检查表将此文件视为必需的 Codex 工作区准备 artifact。
Knowledge dashboard 可以通过 `/api/knowledge/eval-spec` 读取并保存此文件。将该编辑器用于运行后审查或运行前修订；当评估含义改变时，再启动新 timestamp。

CORAL 直接支持此配置：

```yaml
grader:
  eval_version: eval_v1
  profile: quick
  resources:
    # Per-eval job demand.
    cpu_cores: 0
    memory_gb: 0
    gpu_count: 0
    gpu_ids: []
  parallel:
    max_workers: 1
    resources:
      # Total evaluator pool.
      cpu_cores: 0
      memory_gb: 0
      gpu_count: 0
      gpu_ids: []
  profiles:
    quick:
      label: Quick iteration
      timeout: 300
      resources: {cpu_cores: 0, memory_gb: 0, gpu_count: 0, gpu_ids: []}
      args: {profile: quick}
    full:
      label: Full validation
      timeout: 1200
      resources: {cpu_cores: 0, memory_gb: 0, gpu_count: 0, gpu_ids: []}
      args: {profile: full}
```

在 `TaskGrader` 内：

```python
profile = self.profile
version = self.eval_version
level = self.eval_level
space = self.eval_space
args = self.args  # base grader.args merged with selected profile args
resources = self.resources
```

对于多指标评估，返回一个 `ScoreBundle`，其中 `aggregated` 是标量调度分数，每个 public metric 放在 `scores` 中。优先使用：

```python
return self.report_score(
    total_score,
    explanation="scalar score explanation",
    accepted=total_score >= min_score,
    acceptance={"min_score": min_score, "observed_score": total_score},
    metrics={
        "accuracy": {
            "value": accuracy,
            "direction": "maximize",
            "explanation": "Prediction correctness on the scoring split.",
        },
        "latency": {
            "value": latency,
            "direction": "minimize",
            "explanation": "End-to-end runtime under the eval harness.",
        },
    },
    message_for_agent="Accuracy improved; latency remains behind top attempts.",
)
```

这是 eval-module protocol 的具体形式：grader 通过 agent codebase 接收 candidate method，返回一个标量调度分数，并在结构化 metrics 中保留各维度值。当 `TaskGrader` 可以通过 `report_score(...)` 表达同样信息时，不要创建单独的临时 “dict-returning eval” 协议。

失败时返回：

```python
return self.fail_report(
    error_message="solution.py exited with code 1",
    error_type="runtime_error",
    stage="run_cases",
    log_path="eval_logs/<attempt>/stderr.txt",
)
```

CORAL 持久化：

```json
{
  "metadata": {
    "aggregated_score": 0.82,
    "score_components": {
      "breakthrough": {"value": 0.91, "explanation": "..."},
      "guardrail": {"value": 0.73, "explanation": "..."}
    },
    "eval_report": {
      "status": "success",
      "accepted": true,
      "score": {"total": 0.82, "rank": 3, "top_k": []},
      "self_history": {},
      "baselines": [],
      "metrics": {}
    }
  }
}
```

daemon 还会把该 report 的紧凑文本渲染追加到 attempt feedback 中，使 agent 在每次 eval 后都能看到 total score、accepted status、rank、top-5、self history、baselines 和 metric ranks。失败 report 包含 stage、error type、error message 和 log path。

Dashboard 可以绘制总分或任何具名 score component。按时间顺序的图表展示优化进展和 running-best 线。按分数排序的图表只展示排名分布；不要把它解释为时间进展。

## 资源协议

Codex 可以使用 `grader.resources` 表示默认的每次评估 job 需求，并使用 profile 级 `resources` 覆盖 quick/full/stress：

- `cpu_cores`：建议 CPU 核心预算。
- `memory_gb`：建议内存预算。
- `gpu_count`：建议 GPU 数量。
- `gpu_ids`：具体 GPU ID；非空时，CORAL 设置 `CUDA_VISIBLE_DEVICES`。

只将 `grader.parallel.resources` 作为面向用户的 evaluator 总预算暴露。daemon 会在派生 worker 容量和该资源池都有容量时调度 pending job。当 `gpu_ids` 可用时，CORAL 为每个 job 分配不相交的 GPU ID slice，使并发 eval 不会默认使用同一设备。

`TaskGrader.run_program()` 和 `TaskGrader.run_script()` 会把这些环境变量注入子进程：

- `CORAL_CPU_CORES`
- `CORAL_MEMORY_GB`
- `CORAL_GPU_COUNT`
- `CORAL_GPU_IDS`
- `CUDA_VISIBLE_DEVICES`
- `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `NUMEXPR_NUM_THREADS`

这些是标准契约，不是强隔离。需要严格限制时，在任务 grader 中用 Docker、Slurm、cgroups 或 GPU scheduler 强制执行。

## Profiles

使用稳定名称：

- `quick`：便宜、频繁迭代；通常应预测 full-eval 排序。
- `medium`：更强信号，中等成本。
- `full`：最终或接近最终验证。
- `stress`：稳健性、隐藏 case、反作弊、分布偏移。

在 UI 中暴露 profile 名称。不要向普通用户暴露原始脚本路径。

Quick/full/stress profile 必须保持相同的内部计分机制、指标定义和聚合规则。如果 `quick` 使用更少样本或 seed，它的 report 应通过 profile label、feedback 或 metric explanation 明确较低置信度。

## 进度协议

当 eval 持续时间不可忽略时，调用：

```python
self.report_progress(current=i, total=n, phase="evaluate", message=f"case {i}/{n}")
```

CORAL 将结构化进度事件写入：

```text
.coral/public/eval_logs/<attempt_hash>/progress.jsonl
```

每一行：

```json
{"type":"progress","job_id":"<attempt_hash>","phase":"evaluate","current":42,"total":100,"percent":0.42,"message":"case 42/100","timestamp":"2026-06-20T00:00:00Z","eval_version":"eval_v1","eval_profile":"quick"}
```

对于无法导入 grader instance 的外部脚本，使用 `scripts/write_eval_progress.py` 写入同一 JSONL schema。

Dashboard 通过 `/api/evals` 暴露 queued/evaluating job，并在 Overview evaluator panel 中渲染它们。优先使用此协议，而不是解析 tqdm 文本。

## 可比性

不要直接比较：
- `quick` vs `full`
- `eval_v1` vs `eval_v2`
- old grader code vs edited grader code

要跨 eval version 比较 candidate，应在同一冻结评估下重新运行这些 candidate。

运行后审查期间，`/api/review` 会标记混用 eval version/profile 和缺失 eval identity。它也会标记 attempt 被计分后对 `eval_spec.md` 的编辑。将这些标记视为递增 `grader.eval_version`、重新运行选定 attempt，或在做跨运行主张前启动新 timestamp 的理由。
`/api/review` 读取 `eval_spec.md` 和 public baseline attempt 的 public knowledge snapshot，因此它的证据包应与 `/api/control/readiness` 匹配。
