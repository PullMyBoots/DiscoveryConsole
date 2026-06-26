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
    │   ├── attempts/
    │   ├── skills/
    │   ├── agents/
    │   ├── logs/
    │   ├── control/
    │   └── eval_logs/
    └── private/
```

Every timestamp must be interpretable without relying on mutable external task files. If this is too expensive for large datasets/repos, store a manifest with immutable paths, checksums, commits, or object-store IDs.

After Codex creates or modifies a task workspace, timestamp workspace, eval
script/spec, knowledge index, baseline artifact, or agent initialization bundle,
Codex must run the skill validation gate:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/coral-research-workbench/scripts/validate_workspace.py" \
  --task-dir <task-dir> \
  --run-dir <timestamp>/.coral
```

Use `--task-dir` alone before a timestamp exists. The script wraps
`coral validate <task-dir>` to dry-run the task grader and
`coral validate --run-dir <timestamp>/.coral` to check frozen timestamp
readiness. The latter uses the same checks as `/api/control/readiness` and
should fail on missing Codex-prepared artifacts.

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
the Overview baseline line. Readiness and Review read baselines from
`.coral/public/attempts/`.

## Knowledge Layout

```text
knowledge/
├── index.md
├── eval_spec.md
├── manuals/
├── external/
│   ├── index.jsonl
│   └── items/
├── practice/
│   └── agents/
├── briefs/
│   └── agent-seeds/
```

`external/index.jsonl` is the external source registry. Use
`coral kb add external <url-or-path> --kind paper|repo|web|doc|dataset --title
"..." --summary "..."` to add sources. CORAL stores source records under
`external/items/`.

`practice/agents/` stores runtime experience: notebooks, eval-linked chain
nodes, score curves, route summaries, and reflections. Agents should read it via
`coral kb index practice --by score|route|agent|metric` and `coral kb read
<id>`, not by browsing directories.

Prepared agent worktrees expose reusable framework instructions as root-level
symlinks:

```text
CORAL_OVERVIEW.md -> <shared-dir>/knowledge/manuals/coral-overview-cli.md
CORAL_LOOPS.md    -> <shared-dir>/knowledge/manuals/agent-loops.md
```

`CLAUDE.md` and `AGENTS.md` should point to these files as an information map;
the reusable content belongs in the shared manuals.

`eval_spec.md` is the scoring trust argument Codex prepares before launch. It
should cover breakthrough metrics, guardrail metrics, anti-cheating and
overfitting checks, the scalar score formula, and the purpose of each eval
profile. The control panel Readiness checklist expects this file.

External sources use `active` or `archived` status. Archive sources with
`coral kb remove <src-id>` or the dashboard source action.

The dashboard Knowledge view reads `external/index.jsonl` and practice indexes.

The dashboard Review panel is backed by `/api/review`. It summarizes best
attempts, baseline delta, eval identity, failed/pending evals, knowledge
counts, readiness, and suggested review actions. Persist durable conclusions
through `coral kb note` or `coral kb archive --attempt <hash>`.

The dashboard can update manifest source status through
`/api/knowledge/sources/status`. It does not delete filesystem source files or
rewrite source knowledge.

When the dashboard creates a new timestamp from a stopped run, CORAL should
promote active external sources and selected practice summaries through the
index-first knowledge model. The source timestamp remains unchanged.
The dashboard and API should block this action while the current manager or
any recorded agent process is alive; pause or stop the run before forking.

## Agent Initialization Bundles

Codex owns the launch bundle. Store differentiated runnable initialization plans and first-eval scripts here:

```text
knowledge/briefs/
└── agent-seeds/
    ├── agent-1.md
    ├── agent-1.eval.sh
    ├── agent-2.md
    ├── agent-2.eval.sh
    └── agent-3.md
```

The control panel Agent Plan preview reads these files through
`/api/control/plan`. Readiness requires enough agent initialization plans for the
configured agent count.

Plan files should begin with a `#` heading and then a concise technical route,
runnable first step, first eval script path, avoid list, and evolution rule.
Eval scripts must be executable and submit an official `coral eval`. These are
starting technical bundles.
Do not make the user edit these in the control panel; regenerate the workspace
plan with Codex if the plan is poor.

Use `scripts/prepare_agent_plan.py` to materialize these files. Codex can first
write a JSON plan with `agents`, then run:

```bash
python scripts/prepare_agent_plan.py knowledge --plan plan.json --force
```

For an initial placeholder, use:

```bash
python scripts/prepare_agent_plan.py knowledge --agents 4
```

## Control Notes

`.coral/public/control/next_instruction.md` stores the user feedback or
steering note to inject on the next resume. It is run-scoped: keep it inside the
timestamp so the instruction applies to this experiment site only.
