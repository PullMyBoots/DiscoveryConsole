# Control Panel Boundaries

Use this reference when deciding which CORAL fields belong in the user panel.

## User-Facing Controls

Show these as simple controls:

- task name and workspace path: read-only
- executor: Codex, Claude Code, OpenCode, etc.
- model
- reasoning effort via `agents.runtime_options.model_reasoning_effort`
- total runtime/deadline via `run.max_runtime_seconds`
- network permission
- eval profile
- total evaluator CPU/GPU/memory budget via `grader.parallel.resources`
- score chart metric, order, and range controls

## Codex-Owned Setup

Do not expose these as ordinary panel edits:

- exact agent count after workspace generation
- `agents.max_turns` as a normal runtime budget control
- per-agent initial technical direction
- per-agent first-eval scripts
- grader entrypoint
- grader direction (`maximize` / `minimize`)
- raw setup commands
- per-eval resource demand (`grader.resources` and profile resource overrides)
- private grader files
- baseline implementation details
- knowledge file placement

Show these as a read-only plan preview. The Agent Plan reads initialization bundles from
`.coral/public/knowledge/briefs/agent-seeds/*.md`. If the user dislikes the
plan, Codex should regenerate the workspace/plan before launch.

The Knowledge panel may let the user capture review notes and proposed sources,
and mark run-global manifest references as accepted/rejected/archived. It must
not silently delete source files or mutate source knowledge.
It may also expose `.coral/public/knowledge/eval_spec.md` as a markdown editor
so the user/Codex can review or revise the trust argument before a fresh
timestamp run. Saving this file should not mutate prior attempts or silently
change their recorded eval version/profile.

The Overview chart may expose:

- metric selection from total score or `metadata.score_components`
- Order `Time` for chronological progress and running-best
- Order `Score` for score-sorted rank distribution
- range presets and explicit 1-based range input

The Overview agent cards should expose:

- visible state: `work_loop`, `reflect_loop`, `waiting eval`, `paused`, or
  `stopped`
- `evaluating` means the grader is currently running that attempt
- `waiting` means the attempt is queued behind another grader job
- active duration, time since last output, and status duration where available
- current queued/running eval job with progress when one belongs to that agent
- latest few attempts with status, commit, title, and the currently selected
  score metric

## Locked After Start

Once agents have run, ordinary UI should not mutate:

- agent count
- executor/runtime backend for an existing agent
- worktree locations
- grader direction used to interpret scores
- eval version used for existing attempts

Enforce these locks in the API, not only in the frontend. The control panel
save path should preserve Codex-owned fields from the existing `config.yaml`.
Likewise, Run/Resume should be blocked in the API while Readiness is `missing`;
do not rely only on a disabled browser button.

Allow safe changes:

- pause/resume/stop
- extend or shorten deadline
- change future eval profile, worker count, and resource budget if explicitly
  recorded; these apply to future eval waves, not a job already running
- add a user note or instruction for the next resume via
  `.coral/public/control/next_instruction.md`
- stop or resume an individual agent as an advanced action
