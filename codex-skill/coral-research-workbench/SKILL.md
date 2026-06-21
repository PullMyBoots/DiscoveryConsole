---
name: coral-research-workbench
description: Build and operate a Codex-prepared CORAL research workbench. Use when the user wants Codex to turn a research idea into a runnable CORAL workspace, prepare knowledge/baselines/eval profiles/agent briefs, adapt CORAL for multi-agent scientific search, or package the workflow as a repeatable Codex skill.
---

# CORAL Research Workbench

Use this skill to make Codex the workspace architect and CORAL the execution engine. The user should tune high-level controls; Codex should prepare the concrete files, evals, knowledge base, and agent/island plan before the user clicks run.

## Operating Contract

1. Treat each CORAL timestamp as a frozen experiment site.
2. Keep one logical knowledge base per run at `.coral/public/knowledge/`; use `.coral/public/notes/` only as a compatibility alias for `knowledge/notes/`.
3. Do not ask the user to hand-edit per-agent internals during normal startup. Codex owns agent briefs, initial technical directions, baseline scripts, and workspace consistency.
4. Expose only high-level knobs to the user: executor, model, reasoning effort, total runtime, eval profile, network permission, resource budget, single/multi-island mode, migration on/off and cadence, heartbeat intensity.
5. Never compare scores across eval versions unless the run records the eval version/profile and the comparison is explicitly normalized or re-run under one frozen eval.
6. Treat `run.max_runtime_seconds` as the user-facing runtime knob. Do not expose `agents.max_turns` as the ordinary way to decide how long a research run should continue.

## Workflow

### 0. Check CORAL availability

Before preparing a workspace, verify that the execution engine is installed:

```bash
python scripts/check_coral_install.py --json
```

If this reports `status: missing`, tell the user that the full CORAL tool must
be installed before launch and use the install command printed by the script.
Do not pretend the Codex skill alone can run CORAL; the skill is the workflow
adapter, while the repository/CLI is the execution engine.

### 1. Convert the research idea into a task spec

Produce a short written spec with:
- research objective and expected artifact
- baseline method(s)
- breakthrough metrics
- guardrail metrics
- anti-cheating and overfitting checks
- eval cost constraints
- intended final claim and what evidence would support it

### 2. Prepare the workspace

Create or update the CORAL task directory:

```text
<task>/
├── task.yaml
├── seed/
├── grader/
└── knowledge/
```

Initialize `knowledge/` with the script in `scripts/prepare_knowledge.py` when a skeleton is needed. Put startup papers, repos, docs, datasets, method briefs, and task context there before launching CORAL.
Add each startup source to `knowledge/manifest.jsonl` with its local path,
origin URL, provenance, status, and version/checksum when available; the
dashboard Knowledge view surfaces both manifest entries and files under
`knowledge/sources/`.
Fill `knowledge/eval_spec.md` before launch. It should explain the
breakthrough metrics, guardrail metrics, anti-cheating/overfitting checks,
scalar score formula, and eval profile purpose. This is the human-readable
trust argument for why a high score means the candidate is actually better.

Read `references/workspace-contract.md` before changing the run layout or knowledge paths.

### 3. Prepare evals before agents run

Implement eval as a versioned grader with at least one fast iteration profile. Prefer:

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
      # Total evaluator pool available to CORAL.
      cpu_cores: 0
      memory_gb: 0
      gpu_count: 0
      gpu_ids: []
  profiles:
    quick:
      timeout: 300
      resources: {cpu_cores: 0, memory_gb: 0, gpu_count: 0, gpu_ids: []}
      args: {profile: quick}
    full:
      timeout: 1200
      resources: {cpu_cores: 0, memory_gb: 0, gpu_count: 0, gpu_ids: []}
      args: {profile: full}
```

If the task needs multiple profiles, use `quick`, `medium`, `full`, and `stress` as user-facing names. In a `TaskGrader`, use `self.profile`, `self.eval_version`, merged `self.args`, and `self.report_progress(...)`. For multi-dimensional evals, return a `ScoreBundle` with `aggregated` set to the single scheduling score and named component scores in `scores`; CORAL stores them as `metadata.score_components` for dashboard metric selection.

When Codex records a baseline score, mark the resulting attempt with
`metadata.baseline: true` or `metadata.reference: "baseline"` plus an optional
`metadata.baseline_name`. The Overview chart renders these as horizontal
`Baseline: <name>` reference lines for the selected score metric.
After Codex has run the baseline and computed its scalar score, use
`scripts/record_baseline_attempt.py` to write the baseline attempt into the
prepared timestamp:

```bash
python scripts/record_baseline_attempt.py results/<task>/<timestamp>/.coral \
  --score 0.72 \
  --name seed \
  --components '{"breakthrough":0.8,"guardrail":{"value":1.0}}'
```

This script reads `grader.eval_version` and `grader.profile` from
`.coral/config.yaml` unless overridden, writes
`.coral/public/attempts/baseline-<name>.json`, and satisfies the Readiness
baseline check. In multi-island runs, keep this baseline as a run-global
public artifact rather than copying it into one island; Readiness and Review
both include `.coral/public/attempts/` baselines alongside island attempts.

Codex may set `grader.resources` and profile-level `resources` as internal
per-eval job hints when the grader needs them. Do not expose those fields as
ordinary user controls. The dashboard should expose one high-level evaluator
resource budget backed by `grader.parallel.resources`; CORAL derives evaluator
concurrency and GPU assignment from that pool, while the grader still receives
the selected per-job hints through `self.resources` and the standard resource
environment variables. Treat these as the standard resource contract; add
Docker/Slurm/cgroups enforcement inside the task grader when hard limits are
required.

Read `references/eval-contract.md` before writing or modifying graders.

### 4. Generate the agent/island plan

Codex should generate the concrete plan. The user should not manually add/delete agents in the normal control panel.

For single-island mode, generate differentiated agent briefs under `knowledge/briefs/agent-seeds/`.

For multi-island mode, generate:
- an island theme for each island
- 2-5 distinct agent briefs per island, depending on budget
- the migration policy defaults

Never configure more islands than planned agents. A multi-island run needs at
least one agent per island; the control panel and readiness checks should
reject `islands.count > planned agent count`, empty islands, and agent briefs
that reference unknown island IDs.

Use `scripts/prepare_agent_plan.py` to write the plan files after Codex has
chosen the concrete routes. It can create placeholders from `--agents` and
`--islands`, or consume a Codex-generated JSON plan with `--plan` and write
`knowledge/briefs/agent-seeds/*.md` plus `knowledge/briefs/islands/*.md`.

Only after the plan is generated should the user tune runtime/model/resource knobs.

Read `references/control-panel-boundaries.md` before deciding what to expose in UI.

Use `run.max_runtime_seconds` for the control-panel runtime limit:

```yaml
run:
  max_runtime_seconds: 3600
```

`0` means no run-level wall-clock deadline. Positive values stop the whole CORAL manager, all agents, and the grader daemon when the active runtime reaches the limit.

### 5. Launch and supervise

Start CORAL only after:
- `coral validate <task-dir>` passes
- `coral validate --run-dir <timestamp>/.coral` reports no missing readiness
  checks for the prepared timestamp
- seed/baseline produces a recorded score
- knowledge index exists
- eval version/profile are recorded
- control panel displays only user-safe high-level knobs

The control panel exposes a Readiness checklist backed by
`/api/control/readiness`. Treat missing checks as a Codex workspace-preparation
problem, not as fields the user should hand-edit in the browser. The checklist
expects a valid `config.yaml`, `grader.entrypoint`, recorded eval
version/profile, `.coral/public/knowledge/eval_spec.md`,
`.coral/public/knowledge/manifest.jsonl`, at least one baseline attempt marked
with `metadata.baseline: true` or `metadata.reference: "baseline"`, and agent
seed briefs under `.coral/public/knowledge/briefs/agent-seeds/*.md`. In
multi-island mode it also
expects island theme briefs under `.coral/public/knowledge/briefs/islands/*.md`
or `island-themes/*.md`, and at least one agent seed brief assigned to each
configured island.
The Run/Resume action must be blocked while readiness status is `missing`;
warnings may still be user-overridden after review, but missing artifacts mean
Codex has not finished preparing the timestamp.
The CLI run-dir validation uses the same readiness builder as the web endpoint,
so terminal preflight and dashboard readiness should agree.

The control panel also exposes a read-only Agent Plan backed by
`/api/control/plan`. It reads generated island themes from
`.coral/public/knowledge/briefs/islands/*.md` or
`island-themes/*.md`, plus generated agent briefs from
`.coral/public/knowledge/briefs/agent-seeds/*.md`. Use it to let the user
inspect Codex's initial technical directions before launch; do not use it as
an editing surface for adding/removing agents.
At agent startup, CORAL injects the matching agent seed brief and island theme
into that agent's instruction file as a "Codex-prepared starting route". After a
multi-island run has started, `/api/control/plan` and readiness checks may read
the copied plan from `islands/<id>/knowledge/briefs/` when `public/knowledge`
does not hold the active plan.

During runs, let the user pause/resume the whole run and inspect status. The whole-run Pause action preserves the current timestamp and writes a manual stopped state that can be resumed later; the backend route may remain `/api/control/stop` for compatibility, but the dashboard should label it as Pause. Per-agent intervention should be an explicit advanced action, not a default editing surface. The dashboard may stop/resume one live agent through `.coral/public/control/agents/<agent_id>.json`; the manager consumes that desired state, persists `agent_state.json`, and keeps manual stops separate from crash-burst PAUSED state. A targeted dashboard message uses the same file with `action: prompt`; the manager consumes it once, interrupts/resumes only that agent with the message, then clears the action so it is not replayed after a restart.

The Overview dashboard should expose runtime observability directly: run-level
elapsed/remaining/limit time, plus per-agent active duration, time since last
output, visible agent state (`active`, `idle`, `evaluating`, `waiting`,
`heartbeat`, `paused`, `stopped`), queue/eval/heartbeat state duration when
available, and model-usage cost signals from agent logs. `evaluating` and
`waiting` should take display priority over `heartbeat` when the same agent
has a pending grader job. `/api/status` should expose each agent's `island_id`
when available so the dashboard can group live agent cards by island in
multi-island runs. It should also expose run-level and per-agent `usage` with
input/output/cache creation/cache read/total tokens, cost, and cache hit rate;
the dashboard should show Tokens, Cost, and Cache hit without requiring users
to open raw logs. Agent cards should also summarize the agent's current queued
or running eval job with progress, plus the latest few attempts and their
selected score metric, so users can see each agent's iteration path without
opening raw logs.

The Overview score chart should let the user inspect both chronological
optimization progress and rank distribution: use Order `Time` for eval-time
evolution with the running-best line, Order `Score` for score-sorted ranking,
and View/Range controls for All, Last 50, Last 20, or explicit 1-based ranges
such as `5-20`.

If the user gives feedback while a run is stopped or paused, save it as the
next resume instruction instead of editing agent internals. The control panel
stores this at `.coral/public/control/next_instruction.md`; `coral resume`
injects it into every resumed agent via `--instruction-file`.

Once a timestamp has activity, lock executor/runtime backend, island count,
grader direction, and eval version. Keep model, eval profile, resource budget,
deadline, migration toggle, and next-resume instruction editable.
The grader daemon reloads `config.yaml` before each pending-eval drain, so
changes to eval profile and evaluator resources apply to future eval waves;
already-running eval jobs keep the config/resource lease they started with.
Do not expose grader direction as an ordinary user knob: it is part of the
eval meaning and should be changed only by revising the eval design and
starting a new timestamp.

Expose reasoning effort as a high-level control backed by
`agents.runtime_options.model_reasoning_effort`. Expose heartbeat cadence as a
preset that updates the standard `reflect`, `consolidate`, `pivot`, and
`lint_wiki` heartbeat `every` values instead of asking users to edit raw YAML.
Expose network permission as `Network Access`, backed by `agents.research` and
mirrored to `agents.warmstart.enabled`; this controls WebSearch/WebFetch-style
agent research permissions and the optional warm-start research pass.

### 6. Review and promote knowledge after stop

After a run stops, review:
- best attempts and failure modes
- eval reliability and possible reward hacking
- new sources in `knowledge/inbox/`
- notes worth promoting back to the task-level `knowledge/`
- whether to revise eval version before the next timestamp

Use `/api/review` or the Knowledge dashboard Review panel as the first
post-run audit surface. It summarizes best attempt, baseline delta, failed or
pending evals, eval version/profile identity, knowledge counts, model usage,
readiness, review flags, and recommended next actions. Treat this as evidence
for human/Codex review, not as an automatic scientific conclusion. Review
flags include the case where `.coral/public/knowledge/eval_spec.md` was edited
after attempts were scored; treat that as a reason to bump `grader.eval_version`
or re-run selected attempts under one frozen eval before making claims.
In multi-island runs, Review also reads island-local eval specs and run-global
public baselines, so the post-run audit aligns with the Readiness checklist.
Use the Knowledge dashboard Eval Spec editor or `/api/knowledge/eval-spec` to
inspect and revise `.coral/public/knowledge/eval_spec.md` during review. If the
meaning of the eval changes, start a new timestamp before producing new
attempts.

Use the Knowledge dashboard Capture area to persist review state before the
next run. Review notes are written to
`.coral/public/knowledge/notes/<category>/` and proposed references are
appended to `.coral/public/knowledge/manifest.jsonl` with `status:
"proposed"`. In multi-island runs these public/global artifacts are still
shown in the aggregated Knowledge view and should be treated as human/Codex
run-level review material, not as one island's private scratchpad.

During review, mark run-global manifest references as `accepted`, `rejected`,
or `archived` from the Knowledge dashboard or `/api/knowledge/sources/status`.
These status changes are review metadata on the manifest entry
(`reviewed_by`, `reviewed_at`); they do not rewrite island-private knowledge or
delete filesystem source files. Accepted sources are candidates to promote into
the next task-level `knowledge/` snapshot.

When eval changes, start a new timestamp and record the new version.
Use the dashboard `New timestamp` action when the user wants a fresh frozen
experiment site from the current stopped run. It creates a clean timestamp with
the current config, config_dir, snapshots, and prelaunch shared knowledge/skills
but without attempts, logs, run_state, or agent control files. During this
fork, the copied knowledge manifest is promoted: entries with `status:
"accepted"` and unmarked startup entries are kept, while `proposed`,
`rejected`, and `archived` entries and their copied local files are removed from
the new timestamp. Accepted entries whose local files still live under `inbox/`
are moved to `sources/<category>/` in the new timestamp and record
`promoted_from`. Treat the new timestamp as a draft that Codex may further
prepare before the user clicks Run. Do not create a new timestamp while the
current manager or any recorded agent process is alive; pause or stop the run
first so the fork does not copy half-written runtime state.
