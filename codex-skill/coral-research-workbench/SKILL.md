---
name: coral-research-workbench
description: Build and operate a Codex-led, human-in-the-loop CORAL research workbench. Use when Codex must turn a research idea into a runnable CORAL workspace, decide whether the idea is ready for multi-agent search, prepare knowledge/baselines/eval profiles/runnable agent initialization plans and eval scripts, guide user interaction before/during/after CORAL runs, or package the workflow as a repeatable Codex skill.
---

# CORAL Research Workbench

Use this skill to make Codex the bridge between the user and CORAL. The user owns high-level judgment, scientific taste, risk tolerance, and final acceptance. Codex owns research framing, workspace preparation, implementation, analysis, and operational discipline. CORAL owns the lightweight runtime shell: prepared agent processes, worktrees, eval submission, compute scheduling, knowledge CLI, and dashboard rendering.

## Responsibility Contract

1. Treat Codex as the user's research operator, not a YAML assistant.
2. Keep two questions active throughout the task:
   - Is this a real problem worth optimizing?
   - Is the current research path feasible, credible, and evaluable?
3. Do not enter CORAL iteration before the research frame is clear enough to judge progress.
4. Let the user tune high-level controls; Codex prepares concrete files, evals, knowledge, baselines, agent routes, and workspace consistency.
5. Treat CORAL results as evidence to audit with the user, not as automatic scientific conclusions.
6. Do not make the user act as a YAML author, grader author, or workspace maintainer. Ask for research judgment; implement the mechanics yourself.

Read `references/interaction-protocol.md` before changing the human/Codex/CORAL workflow or deciding whether a user request should launch CORAL.

## Non-Negotiable Gates

Do not launch CORAL until Codex has prepared and the user can inspect:

- a specific research objective and expected artifact
- essential literature, reusable projects, tools, datasets, and task context
- baseline method(s) and baseline performance
- breakthrough metrics, guardrail metrics, anti-cheating and overfitting checks
- one selected evaluation level (L1, L2, or L3) that matches the task; these are alternatives, not simultaneous modes for one run
- a scalar score where higher means better for CORAL scheduling
- eval profiles with controlled cost, at least a fast iteration profile
- runnable per-agent initialization bundles: differentiated technical routes and executable first-eval scripts
- a valid timestamp workspace that records eval version/profile and knowledge provenance

If any gate is missing, treat it as a Codex preparation problem. Ask only for the smallest user decision needed to continue.

After Codex changes any task workspace, timestamp workspace, eval script/spec,
knowledge index, baseline artifact, agent initialization bundle, or dashboard
launch configuration, it must run the workspace validation gate before treating
the change as complete:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/coral-research-workbench/scripts/validate_workspace.py" \
  --task-dir <task-dir> \
  --run-dir <timestamp>/.coral
```

Use `--task-dir` alone before a timestamp exists. Use both arguments once a
timestamp has been prepared. If the validation fails, fix the workspace and rerun
the same command; do not launch or resume CORAL from a failed workspace.

## Two-Loop Model

Use CORAL as a two-loop research system:

- Inner loop: CORAL agents search within a frozen timestamp, submit attempts, receive eval scores, share knowledge, and improve the scalar score.
- Outer loop: the user and Codex review whether the score is trustworthy, whether the eval is valid, whether knowledge should be promoted, and whether the next run should continue, pivot, or fork a new timestamp.

The outer loop controls scientific credibility. Never collapse it into "highest score wins."

## Workflow

### 0. Intake and Frame

First identify the user's current state:

- vague direction with no concrete research question
- clear direction but no diagnosed bottleneck
- diagnosed method or system problem
- mature method needing refinement or validation

Then produce a short research frame with objective, baseline, evidence needed, risks, evaluation plan, and the expected final claim. Start a CORAL run when that frame is concrete enough for scored iteration.

Read `references/research-framing.md` when turning a vague or partially formed idea into a runnable research task.

### 1. Check CORAL Availability

Before preparing a workspace, verify that the execution engine is installed:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/coral-research-workbench/scripts/check_coral_install.py" --json
```

If this reports `status: missing`, tell the user that the full CORAL tool must be installed before launch and use the install command printed by the script. Do not pretend the Codex skill alone can run CORAL; the skill is the workflow adapter, while the repository/CLI is the execution engine.

### 2. Prepare Knowledge and Workspace

Create or update the CORAL task directory:

```text
<task>/
├── task.yaml
├── seed/
├── grader/
├── knowledge/
└── results/
```

Use `scripts/prepare_knowledge.py` when a skeleton is needed. Keep the memory
system index-first and small:

- `knowledge/eval_spec.md`: the scoring contract and safety rules.
- `knowledge/manuals/`: short framework manuals.
- `knowledge/external/index.jsonl` plus `knowledge/external/items/`: static external papers, repos, docs, datasets, and web references.
- `knowledge/practice/agents/`: eval-linked notebooks, routes, score curves, and reflections.
- `knowledge/briefs/agent-seeds/`: Codex-prepared starting routes and first-eval scripts.

Add each external source with `coral kb add external <url-or-path> --kind ... --title ... --summary ...`.
Agents should use `coral kb index ...` before `coral kb read <id>` instead of browsing the knowledge filesystem.

Read `references/workspace-contract.md` before changing run layout, knowledge paths, timestamp behavior, or baseline recording.

After creating or modifying this workspace, run the validation gate described in
Non-Negotiable Gates. This is mandatory even when the edits look mechanical.

### 3. Design the Eval Before Agents Run

Treat the eval as the trust foundation. It must make "high score" mean "actually better" as far as possible.

The eval should include:

- breakthrough metrics for what should improve
- guardrail metrics for what must not break
- hard failure checks for invalid outputs, leakage, cheating, overfitting, and format violations
- cost-controlled profiles such as `quick`, `medium`, `full`, and `stress`
- one scalar scheduling score, with named component metrics preserved for review

Choose exactly one evaluation level with the user before writing the grader:

- L1: fixed/open scoring; agents can see and call the scoring function.
- L2: open A-space exploration, hidden B-space ranking eval.
- L3: open A-space plus hidden B-space iteration, with sealed C-space final validation outside the normal CORAL loop.

For one research question, L1/L2/L3 are not three parallel settings and not
runtime tuning knobs. Once the user and Codex have defined what is being
studied and what claim the result should support, the question should match one
level by design. The guiding axis is environment certainty: the more fixed and
closed the target scenario is, the more the task belongs near L1; the more
open, uncertain, or deployment-dependent the scenario is, the more it belongs
near L3.

- L1: use for highly fixed scenarios such as optimizing a known program step,
  kernel, script, or benchmark component. The scoring contract is open and the
  meaningful goal is direct improvement under that contract.
- L2: use when the scenario is still fixed enough to simulate or benchmark
  credibly, but public probes would invite overfitting. Agents may explore
  A-space; hidden B-space decides ranking or acceptance.
- L3: use when the claim must survive open-world or uncertain deployment.
  A/B evidence is useful for search, but a B-space winner may still be a local
  optimum or overfit to the validation regime, so sealed C-space is reserved
  for final human/Codex validation outside the normal agent loop.

When generalization matters, A/B/C are not arbitrary same-distribution splits.
Design them as a graded evidence ladder: A should be cheap and learnable enough
to guide optimization without being toy-like; B should be more representative
and hidden enough to test overfitting; C should be closest to the real target
environment. The gaps should be deliberate but not discontinuous. If A is too
simple or B/C are too far away, agents cannot learn useful directions and the
final evidence becomes hard to interpret.

Do not run the same research question as "try L1, then try L2, then try L3." If
the level judgment was wrong, treat that as a change to the research design:
fork a new task version or timestamp lineage, record the new eval contract, and
do not compare scores as if they came from the same experiment.

Write the human-readable trust argument to `knowledge/eval_spec.md`. If the eval meaning changes after attempts exist, start a new timestamp or re-run selected attempts under one frozen eval before comparing scores.

After writing or editing grader code, run the validation gate. `coral validate <task-dir>` must load the grader entrypoint and execute it on `seed/`. Treat failures to return a scalar, structured metrics, or an `eval_report` through `TaskGrader.report_score(...)` / `fail_report(...)` as eval-contract bugs, not as agent problems.

Read `references/eval-design.md` for evaluation philosophy and `references/eval-contract.md` before writing or modifying graders.

### 4. Generate Runnable Agent Initialization Bundles

Codex owns the launch plan. Do not ask the user to manually add/delete agents or edit per-agent internals in the ordinary control panel.

For every agent, write a runnable initialization bundle:

- one distinct initialization plan under `knowledge/briefs/agent-seeds/`
- one executable first-eval script under `knowledge/briefs/agent-seeds/<agent-id>.eval.sh`
- a concrete first implementation or diagnostic step
- the exact eval command/script to run after that first step and what signal to watch
- what to avoid, including guardrail and overfitting risks
- an evolution rule: start from this plan, then revise the route only from eval feedback and shared evidence

Use `scripts/prepare_agent_plan.py` after Codex has chosen the concrete routes. Only after this plan exists should the user tune runtime/model/resource controls.

Read `references/agent-plan-contract.md` before changing agent initialization or route planning behavior.

### 5. Materialize the Timestamp Workspace

Codex must turn the task directory into a concrete timestamp run before the
human launches agents:

```bash
coral prepare -c <task-dir>/task.yaml
```

This creates the frozen run directory, repo clone, shared `.coral/public`
state, and one isolated worktree per planned agent. It does not start agents
or the grader daemon. After it finishes, validate the prepared timestamp:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/coral-research-workbench/scripts/validate_workspace.py" \
  --task-dir <task-dir> \
  --run-dir <timestamp>/.coral
```

Fix any missing readiness item before launch. A valid launch command points to
the prepared run config, not the original task config:

```bash
coral start -c <timestamp>/.coral/config.yaml
```

### 6. Expose Only User-Safe Controls

The control panel should expose high-level controls:

- executor/backend, model, and reasoning effort
- total runtime via `run.max_runtime_seconds`
- eval profile
- network permission
- total evaluator resource budget
- pause/resume and next-resume instruction

Do not expose raw YAML, grader internals, per-agent initialization scripts, score direction, or low-level resource fields as ordinary user knobs.

Read `references/control-panel-boundaries.md` before deciding what belongs in the UI.

### 7. Launch and Supervise

Start CORAL only after:

- `scripts/validate_workspace.py --task-dir <task-dir> --run-dir <timestamp>/.coral` passes
- seed/baseline produces a recorded score
- knowledge index and eval spec exist
- eval version/profile are recorded
- agent routes are ready

During a run, let the user observe progress, pause/resume the whole run, and optionally send a next-resume instruction. If the user gives feedback while paused or stopped, save run-level steering to `.coral/public/control/next_instruction.md`. For targeted per-agent critique, reset that agent's notebook with `coral kb notebook --agent <agent-id> --set <file> --reason external-adjustment --by codex`. Add or archive external knowledge only through `coral kb add external ...` and `coral kb remove <src-id>`. Do not silently rewrite eval semantics, hidden data, or agent initialization bundles after attempts exist.

Once a timestamp has activity, lock evaluation level, executor/runtime backend,
grader direction, eval version, and route topology. Keep model, eval profile,
resource budget, deadline, and next-resume instruction editable when safe.

### 8. Review, Promote, or Fork

After a run stops, review with the user:

- best attempts and failure modes
- baseline delta and score component behavior
- eval reliability, overfitting risk, and reward hacking risk
- external sources worth keeping active
- practice knowledge worth preserving for the next timestamp
- whether to continue, pivot, change eval, or fork a new timestamp

Use `/api/review` or the Knowledge dashboard Review panel as the first evidence surface. If the eval meaning changes, start a new timestamp and record the new eval version before producing new attempts.

Read `references/run-review-protocol.md` before making post-run claims, promoting knowledge, or forking a timestamp.
