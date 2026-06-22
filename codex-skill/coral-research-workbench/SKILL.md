---
name: coral-research-workbench
description: Build and operate a Codex-led, human-in-the-loop DiscoveryConsole/CORAL research workbench. Use when Codex must turn a research idea into a runnable CORAL workspace, decide whether the idea is ready for multi-agent search, prepare knowledge/baselines/eval profiles/agent briefs, guide user interaction before/during/after CORAL runs, or package the workflow as a repeatable Codex skill.
---

# CORAL Research Workbench

Use this skill to make Codex the bridge between the user and CORAL. The user owns high-level judgment, scientific taste, risk tolerance, and final acceptance. Codex owns research framing, workspace preparation, implementation, analysis, and operational discipline. CORAL owns the inner multi-agent execution loop after the research frame is ready.

## Role Contract

1. Treat Codex as the user's research operator, not a YAML assistant.
2. Keep two questions active throughout the task:
   - Is this a real problem worth optimizing?
   - Is the current research path feasible, credible, and evaluable?
3. Do not enter CORAL iteration before the research frame is clear enough to judge progress.
4. Let the user tune high-level controls; Codex prepares concrete files, evals, knowledge, baselines, agent routes, and workspace consistency.
5. Treat CORAL results as evidence to audit with the user, not as automatic scientific conclusions.

Read `references/interaction-protocol.md` before changing the human/Codex/CORAL workflow or deciding whether a user request should launch CORAL.

## Non-Negotiable Gates

Do not launch CORAL until Codex has prepared and the user can inspect:

- a specific research objective and expected artifact
- essential literature, reusable projects, tools, datasets, and task context
- baseline method(s) and baseline performance
- breakthrough metrics, guardrail metrics, anti-cheating and overfitting checks
- a scalar score where higher means better for CORAL scheduling
- eval profiles with controlled cost, at least a fast iteration profile
- differentiated agent starting routes and, for multi-island runs, island themes
- a valid timestamp workspace that records eval version/profile and knowledge provenance

If any gate is missing, treat it as a Codex preparation problem. Ask only for the smallest user decision needed to continue.

## Two-Loop Model

Use DiscoveryConsole as a two-loop research system:

- Inner loop: CORAL agents search within a frozen timestamp, submit attempts, receive eval scores, share knowledge, migrate results, and improve the scalar score.
- Outer loop: the user and Codex review whether the score is trustworthy, whether the eval is valid, whether knowledge should be promoted, and whether the next run should continue, pivot, or fork a new timestamp.

The outer loop controls scientific credibility. Never collapse it into "highest score wins."

## Workflow

### 0. Intake and Frame

First identify the user's current state:

- vague direction with no concrete research question
- clear direction but no diagnosed bottleneck
- diagnosed method or system problem
- mature method needing refinement or validation

Then produce a short research frame with objective, baseline, evidence needed, risks, evaluation plan, and the expected final claim. Do not create a CORAL run merely because the user has an idea.

Read `references/research-framing.md` when turning a vague or partially formed idea into a runnable research task.

### 1. Check CORAL Availability

Before preparing a workspace, verify that the execution engine is installed:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/coral-research-workbench/scripts/check_coral_install.py" --json
```

If this reports `status: missing`, tell the user that the full DiscoveryConsole/CORAL tool must be installed before launch and use the install command printed by the script. Do not pretend the Codex skill alone can run CORAL; the skill is the workflow adapter, while the repository/CLI is the execution engine.

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

Use `scripts/prepare_knowledge.py` when a skeleton is needed. Put startup papers, repos, docs, datasets, method briefs, and task context under `knowledge/` before launch. Add each source to `knowledge/manifest.jsonl` with provenance and version/checksum when available.

Read `references/workspace-contract.md` before changing run layout, knowledge paths, timestamp behavior, or baseline recording.

### 3. Design the Eval Before Agents Run

Treat the eval as the trust foundation. It must make "high score" mean "actually better" as far as possible.

The eval should include:

- breakthrough metrics for what should improve
- guardrail metrics for what must not break
- hard failure checks for invalid outputs, leakage, cheating, overfitting, and format violations
- cost-controlled profiles such as `quick`, `medium`, `full`, and `stress`
- one scalar scheduling score, with named component metrics preserved for review

Write the human-readable trust argument to `knowledge/eval_spec.md`. If the eval meaning changes after attempts exist, start a new timestamp or re-run selected attempts under one frozen eval before comparing scores.

Read `references/eval-design.md` for evaluation philosophy and `references/eval-contract.md` before writing or modifying graders.

### 4. Generate the Agent and Island Plan

Codex owns the launch plan. Do not ask the user to manually add/delete agents or edit per-agent internals in the ordinary control panel.

Generate differentiated routes:

- for single-island mode, multiple distinct agent seed briefs under `knowledge/briefs/agent-seeds/`
- for multi-island mode, one theme per island plus one or more agent seed briefs per island
- migration defaults when islands are enabled

Use `scripts/prepare_agent_plan.py` after Codex has chosen the concrete routes. Only after this plan exists should the user tune runtime/model/resource controls.

Read `references/agent-plan-contract.md` before changing agent initialization, island topology, or migration behavior.

### 5. Expose Only User-Safe Controls

The control panel should expose high-level controls:

- executor/backend, model, and reasoning effort
- total runtime via `run.max_runtime_seconds`
- eval profile
- network permission
- total evaluator resource budget
- single/multi-island mode before launch
- migration and heartbeat presets
- pause/resume and next-resume instruction

Do not expose raw YAML, grader internals, per-agent initialization scripts, score direction, or low-level resource fields as ordinary user knobs.

Read `references/control-panel-boundaries.md` before deciding what belongs in the UI.

### 6. Launch and Supervise

Start CORAL only after:

- `coral validate <task-dir>` passes
- `coral validate --run-dir <timestamp>/.coral` reports no missing readiness checks
- seed/baseline produces a recorded score
- knowledge index and eval spec exist
- eval version/profile are recorded
- agent routes and island themes are ready

During a run, let the user observe progress, pause/resume the whole run, and optionally send a next-resume instruction. If the user gives feedback while paused or stopped, save it to `.coral/public/control/next_instruction.md`; do not silently rewrite agent internals.

Once a timestamp has activity, lock executor/runtime backend, island count, grader direction, eval version, and topology. Keep model, eval profile, resource budget, deadline, migration toggle, heartbeat preset, and next-resume instruction editable when safe.

### 7. Review, Promote, or Fork

After a run stops, review with the user:

- best attempts and failure modes
- baseline delta and score component behavior
- eval reliability, overfitting risk, and reward hacking risk
- new sources in `knowledge/inbox/`
- notes worth promoting into durable task knowledge
- whether to continue, pivot, change eval, or fork a new timestamp

Use `/api/review` or the Knowledge dashboard Review panel as the first evidence surface. If the eval meaning changes, start a new timestamp and record the new eval version before producing new attempts.

Read `references/run-review-protocol.md` before making post-run claims, promoting knowledge, or forking a timestamp.
