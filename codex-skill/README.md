# CORAL Codex Skill

This directory packages the Codex-facing workflow for CORAL. The
skill teaches Codex how to act as the user's research operator: frame the
problem, design trustworthy evals, prepare knowledge and baselines, generate
runnable agent initialization bundles, and operate CORAL as the execution engine.

The skill does not replace the CLI. It is the human/Codex/CORAL collaboration
layer.

## Install Locally

Link the skill into each research project where Codex will use it:

```bash
DISCOVERYCONSOLE_DIR=/path/to/DiscoveryConsole

cd /path/to/your/research-project
mkdir -p .agents/skills
ln -sfn "$DISCOVERYCONSOLE_DIR/codex-skill/coral-research-workbench" \
  .agents/skills/coral-research-workbench
```

Then start a new Codex session from that research project and ask it to use the
`coral-research-workbench` skill. You may also link the same skill into
`$HOME/.agents/skills/` for global reuse, but the project-local `.agents/skills/`
link is the recommended installation step.

To check whether the execution engine is available:

```bash
python ".agents/skills/coral-research-workbench/scripts/check_coral_install.py" --json
```

## What The Skill Provides

- A Codex/user/CORAL interaction protocol.
- Research framing gates before CORAL launch.
- Eval design guidance for breakthrough metrics, guardrails, anti-cheating,
  overfitting, profiles, and score credibility.
- Workspace, knowledge, baseline, agent-plan, and control-panel contracts.
- Scripts for availability checks, knowledge skeletons, runnable agent
  initialization bundles, baseline records, and eval progress.
- A two-loop workflow where CORAL performs inner search and the user plus Codex
  perform outer review, steering, knowledge promotion, and timestamp forking.

## Syncing During Development

If you edit a linked or installed skill first, mirror it back into the
repository before publishing:

```bash
rsync -a --delete --exclude '__pycache__/' \
  "$HOME/.agents/skills/coral-research-workbench/" \
  codex-skill/coral-research-workbench/
```
