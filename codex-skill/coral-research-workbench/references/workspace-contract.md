# Workspace Contract

Use this reference when creating or changing a CORAL task/run layout.

## Task Directory

```text
<task>/
├── task.yaml
├── seed/
├── grader/
├── knowledge/
└── results/
```

- `task.yaml`: source config prepared by Codex.
- `seed/`: initial code/base project. Agents do not edit this directly.
- `grader/`: versioned eval package.
- `knowledge/`: task-level knowledge prepared before a run. Promote reviewed run knowledge back here after stop.
- `results/`: generated runs.

## Timestamp Run

```text
results/<task-slug>/<timestamp>/
├── snapshots/
│   ├── task.yaml
│   ├── seed/
│   ├── grader/
│   └── knowledge/
├── repo/
├── agents/
└── .coral/
    ├── config.yaml
    ├── public/
    │   ├── knowledge/
    │   ├── notes -> knowledge/notes
    │   ├── attempts/
    │   ├── skills/
    │   ├── agents/
    │   ├── logs/
    │   ├── heartbeat/
    │   ├── control/
    │   ├── roles/
    │   └── eval_logs/
    └── private/
```

Every timestamp must be interpretable without relying on mutable external task files. If this is too expensive for large datasets/repos, store a manifest with immutable paths, checksums, commits, or object-store IDs.

Before launch, Codex should run `coral validate <task-dir>` to dry-run the task
grader and `coral validate --run-dir <timestamp>/.coral` to check the frozen
timestamp readiness. The latter uses the same checks as `/api/control/readiness`
and should fail on missing Codex-prepared artifacts.

After the baseline program or method has been evaluated, record its score as a
timestamp attempt before launch:

```bash
python scripts/record_baseline_attempt.py results/<task>/<timestamp>/.coral \
  --score <scalar-score> \
  --name seed
```

The script writes `.coral/public/attempts/baseline-<name>.json` with
`metadata.baseline: true`, `metadata.reference: baseline`, and the frozen
eval version/profile. This is the baseline artifact expected by Readiness and
the Overview baseline line. In multi-island runs this remains a run-global
public artifact; Readiness and Review read `.coral/public/attempts/` baselines
in addition to island-local attempts.

## Knowledge Layout

```text
knowledge/
├── index.md
├── eval_spec.md
├── manifest.jsonl
├── capsules/
├── maps/
│   └── methods.md
├── packs/
│   ├── global.md
│   └── <agent-id>.md
├── sources/
│   ├── papers/
│   ├── repos/
│   ├── web/
│   ├── docs/
│   └── datasets/
├── notes/
│   ├── research/
│   ├── experiments/
│   ├── synthesis/
│   └── open-questions/
├── briefs/
│   ├── agent-seeds/
│   ├── islands/
│   └── island-themes/
├── inbox/
└── archive/
```

`manifest.jsonl` is the source registry. Each line should identify the source,
local path, origin URL, added_by, added_at, status, and version information
such as commit, DOI, checksum, or doc version. Codex may append new proposed
sources during review. CORAL may rewrite a manifest entry only to update review
metadata such as `status`, `reviewed_by`, and `reviewed_at`.

Keep the knowledge system lightweight. Raw papers, repos, web captures, docs,
and datasets live under `sources/`, but agents should not read them by default.
Codex should compress useful sources into short `capsules/*.md` files and route
agents through `packs/<agent-id>.md`. A packet is the smallest reading set for
one agent: route, must-read capsules, optional capsules, eval targets, and
source rules. If no agent-specific packet exists, agents fall back to
`packs/global.md`. `maps/methods.md` is the compact route map; keep it short and
link to capsules instead of copying source material.

`eval_spec.md` is the scoring trust argument Codex prepares before launch. It
should cover breakthrough metrics, guardrail metrics, anti-cheating and
overfitting checks, the scalar score formula, and the purpose of each eval
profile. The control panel Readiness checklist expects this file.

Use these review statuses:

- `proposed`: candidate source captured during review.
- `accepted`: useful source to preserve or promote into the next task-level snapshot.
- `rejected`: reviewed and not useful for this task.
- `archived`: kept for provenance, but not active guidance.

Use `inbox/` for newly downloaded material that has not been reviewed. Move reviewed artifacts into `sources/` and link them from a research note.
When an inbox source becomes useful guidance, create or update a capsule and
link it from a packet or `maps/methods.md`; do not make raw inbox material an
ordinary agent starting point.

The dashboard Knowledge view reads `manifest.jsonl` and scans `sources/` so
papers, repos, web pages, docs, and datasets are visible even before an agent
turns them into notes.

The dashboard Review panel is backed by `/api/review`. It summarizes best
attempts, baseline delta, eval identity, failed/pending evals, knowledge
counts, readiness, and suggested review actions. Persist durable conclusions
as notes under `knowledge/notes/<category>/`. In multi-island runs, Review
includes run-global public baselines and can read eval specs from the active
island knowledge snapshots when public knowledge is only a global shell.

The dashboard can update run-global manifest source status through
`/api/knowledge/sources/status`. It does not delete filesystem source files or
rewrite island-private knowledge.

When the dashboard creates a new timestamp from a stopped run, CORAL promotes
only active run-global knowledge into the new timestamp. Manifest entries with
`status: accepted` and unmarked startup entries are kept; `proposed`,
`rejected`, and `archived` entries are dropped from the new manifest, and their
copied local `relative_path` files/directories are removed from the new
timestamp copy. Accepted entries whose copied local `relative_path` is under
`inbox/` are moved to `sources/<category>/` and get `promoted_from` metadata.
The source timestamp remains unchanged.
The dashboard and API should block this action while the current manager or
any recorded agent process is alive; pause or stop the run before forking.

## Agent and Island Plan

Codex owns the launch plan. Store differentiated initial directions here:

```text
knowledge/briefs/
├── agent-seeds/
│   ├── agent-1.md
│   ├── agent-2.md
│   └── 0-agent-1.md
└── islands/
    ├── 0.md
    └── 1.md
```

For multi-island mode, prefer `briefs/islands/<island-id>.md` for island themes.
`briefs/island-themes/` is also recognized for compatibility. The control
panel Agent Plan preview reads these files through `/api/control/plan`.
Readiness requires every configured island to have at least one agent seed
brief and rejects briefs that reference unknown island IDs.

Brief files should begin with a `#` heading and then a concise technical
direction. Do not make the user edit these in the control panel; regenerate the
workspace plan with Codex if the plan is poor.

Use `scripts/prepare_agent_plan.py` to materialize these files. The script also
writes `knowledge/packs/<agent-id>.md` so each agent starts from a small reading
packet instead of scanning the whole knowledge tree. Codex can first write a
JSON plan with `islands` and `agents`, then run:

```bash
python scripts/prepare_agent_plan.py knowledge --plan plan.json --force
```

For an initial placeholder, use:

```bash
python scripts/prepare_agent_plan.py knowledge --agents 4 --islands 2
```

## Control Notes

`.coral/public/control/next_instruction.md` stores the user feedback or
steering note to inject on the next resume. It is run-scoped: keep it inside the
timestamp so the instruction applies to this experiment site only.
