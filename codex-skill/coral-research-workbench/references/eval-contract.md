# Eval Contract

Use this reference before writing or changing a CORAL grader for research search.

## Required Properties

1. A grader must return one scalar score for CORAL scheduling.
2. Multi-dimensional metrics must be returned as `ScoreBundle.scores`; CORAL
   records them under attempt `metadata.score_components`.
3. A grader should return `metadata.eval_report` through
   `TaskGrader.report_score(...)` or `TaskGrader.fail_report(...)`. CORAL
   augments this report with rank, top-5, self-history, and baselines after
   the attempt is finalized.
4. The scalar score must combine:
   - breakthrough metrics: what should improve
   - guardrail metrics: what must not break
   - hard failure checks: cheating, invalid outputs, timeouts, data leakage, format violations
5. The eval version, profile, evaluation level, and evaluation space must be
   recorded on every attempt.

## Evaluation Level

Choose one level with the user before writing the grader. The choice depends on
the task and intended claim; L1/L2/L3 are alternatives for a fixed task, not
three simultaneous modes.

- L1: fixed/open scenario. The A-space scoring mechanism is public to agents.
  Use this for tasks where the goal is direct program optimization against a
  known objective.
- L2: open exploration with hidden ranking. Agents can probe A-space, but
  official ranking uses B-space to reduce overfitting to public probes.
- L3: strict research validation. Agents iterate with A/B, while C-space is
  sealed for final human/Codex validation after the CORAL run. CORAL stores the
  C-space assets, but the normal agent eval loop should not expose or run them.

The selected level should be written in `knowledge/eval_spec.md` with the
allowed agent API and the hidden boundary for that task.

Before launch, write the human-readable trust argument to
`knowledge/eval_spec.md`. It must cover:

- agent API: what commands/files the agent may use (`coral eval`, optional
  `coral eval --tune`, `coral run -- <command>`, and any other public A-space
  exploration API)
- evaluation level: L1/L2/L3 and what A/B/C spaces mean for this task
- public metric names, directions, and safe explanations
- acceptance criteria: minimum score, required tests, runtime/memory limits,
  leakage checks, or other hard gates
- anti-cheating and overfitting checks: leakage, invalid outputs, held-out or
  stress cases, and robustness checks
- profile intent: quick/medium/full/stress must use the same scoring mechanism;
  smaller profiles differ only by sample size, seeds, cases, or run count
- feedback report: what the agent sees on success/failure and which details
  must stay hidden

The control panel Readiness checklist treats this file as a required Codex
workspace-preparation artifact.
The Knowledge dashboard can read and save this file through
`/api/knowledge/eval-spec`. Use that editor for post-run review or pre-run
revisions, then start a new timestamp when the eval meaning changes.

CORAL supports this directly:

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

Inside a `TaskGrader`:

```python
profile = self.profile
version = self.eval_version
level = self.eval_level
space = self.eval_space
args = self.args  # base grader.args merged with selected profile args
resources = self.resources
```

For multi-metric evals, return a `ScoreBundle` with the scalar scheduling score
in `aggregated` and each public metric in `scores`. Prefer:

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

This is the concrete form of the eval-module protocol: the grader receives a
candidate method through the agent codebase, returns one scalar scheduling
score, and preserves per-dimension values in structured metrics. Do not create
a separate ad hoc "dict-returning eval" protocol when a `TaskGrader` can express
the same information through `report_score(...)`.

For failures, return:

```python
return self.fail_report(
    error_message="solution.py exited with code 1",
    error_type="runtime_error",
    stage="run_cases",
    log_path="eval_logs/<attempt>/stderr.txt",
)
```

CORAL persists:

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

The daemon also appends a compact text rendering of this report to the attempt
feedback so the agent sees total score, accepted status, rank, top-5, self
history, baselines, and metric ranks after every eval. Failed reports include
stage, error type, error message, and log path.

The dashboard can plot either the total score or any named score component.
Chronological chart order shows optimization progress and the running-best
line. Score-sorted chart order shows rank distribution only; do not interpret
it as temporal progress.

## Resource Protocol

Codex may use `grader.resources` for the default per-eval job demand and
profile-level `resources` for quick/full/stress overrides:

- `cpu_cores`: advisory CPU core budget.
- `memory_gb`: advisory memory budget.
- `gpu_count`: advisory GPU count.
- `gpu_ids`: concrete GPU IDs; when non-empty, CORAL sets `CUDA_VISIBLE_DEVICES`.

Expose only `grader.parallel.resources` as the user-facing total evaluator
budget. The daemon schedules pending jobs while both derived worker capacity
and this pool have capacity. When `gpu_ids` are available, CORAL assigns
disjoint GPU ID slices per job so concurrent evals do not default to the same
device.

`TaskGrader.run_program()` and `TaskGrader.run_script()` inject these environment
variables into child processes:

- `CORAL_CPU_CORES`
- `CORAL_MEMORY_GB`
- `CORAL_GPU_COUNT`
- `CORAL_GPU_IDS`
- `CUDA_VISIBLE_DEVICES`
- `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `NUMEXPR_NUM_THREADS`

These are a standard contract, not hard isolation. For strict limits, enforce
them in the task grader with Docker, Slurm, cgroups, or a GPU scheduler.

## Profiles

Use stable names:

- `quick`: cheap, frequent iteration; should usually predict full-eval ordering.
- `medium`: stronger signal, moderate cost.
- `full`: final or near-final validation.
- `stress`: robustness, hidden cases, anti-cheating, distribution shift.

Expose profile names in UI. Do not expose raw script paths to normal users.

Quick/full/stress profiles must preserve the same internal scoring mechanism,
metric definitions, and aggregation rule. If `quick` uses fewer samples or
seeds, its report should make the lower confidence clear through its profile
label, feedback, or metric explanations.

## Progress Protocol

When eval duration is nontrivial, call:

```python
self.report_progress(current=i, total=n, phase="evaluate", message=f"case {i}/{n}")
```

CORAL writes structured progress events to:

```text
.coral/public/eval_logs/<attempt_hash>/progress.jsonl
```

Each line:

```json
{"type":"progress","job_id":"<attempt_hash>","phase":"evaluate","current":42,"total":100,"percent":0.42,"message":"case 42/100","timestamp":"2026-06-20T00:00:00Z","eval_version":"eval_v1","eval_profile":"quick"}
```

For external scripts that cannot import the grader instance, use `scripts/write_eval_progress.py` to write the same JSONL schema.

The dashboard exposes queued/evaluating jobs through `/api/evals` and renders them in the Overview evaluator panel. Prefer this protocol over parsing tqdm text.

## Comparability

Do not directly compare:
- `quick` vs `full`
- `eval_v1` vs `eval_v2`
- old grader code vs edited grader code

To compare candidates across eval versions, re-run the candidates under the same frozen eval.

During post-run review, `/api/review` flags mixed eval versions/profiles and
missing eval identity. It also flags `eval_spec.md` edits made after attempts
were scored. Treat those flags as reasons to bump `grader.eval_version`, re-run
selected attempts, or start a new timestamp before making a cross-run claim.
`/api/review` reads the public knowledge snapshot for `eval_spec.md` and public
baseline attempts, so its evidence bundle should match `/api/control/readiness`.
