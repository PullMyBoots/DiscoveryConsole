# Eval Contract

Use this reference before writing or changing a CORAL grader for research search.

## Required Properties

1. A grader must return one scalar score for CORAL scheduling.
2. Multi-dimensional metrics must be returned as `ScoreBundle.scores`; CORAL
   records them under attempt `metadata.score_components`.
3. The scalar score must combine:
   - breakthrough metrics: what should improve
   - guardrail metrics: what must not break
   - hard failure checks: cheating, invalid outputs, timeouts, data leakage, format violations
4. The eval version and profile must be recorded on every attempt.

Before launch, write the human-readable trust argument to
`knowledge/eval_spec.md`. It must cover:

- breakthrough metrics: what higher score should mean
- guardrail metrics: what must stay above a floor
- anti-cheating and overfitting checks: leakage, invalid outputs, held-out or
  stress cases, and robustness checks
- scalar score: how metrics become the single CORAL scheduling score
- profile intent: why `quick` is cheap enough for iteration and how `full` or
  `stress` validates the claim

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
args = self.args  # base grader.args merged with selected profile args
resources = self.resources
```

For multi-metric evals, return a `ScoreBundle` with the scalar scheduling score
in `aggregated` and each public metric in `scores`. CORAL persists:

```json
{
  "metadata": {
    "aggregated_score": 0.82,
    "score_components": {
      "breakthrough": {"value": 0.91, "explanation": "..."},
      "guardrail": {"value": 0.73, "explanation": "..."}
    }
  }
}
```

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
For multi-island runs, `/api/review` reads the active island knowledge
snapshots for `eval_spec.md` and includes run-global public baseline attempts,
so its evidence bundle should match `/api/control/readiness`.
