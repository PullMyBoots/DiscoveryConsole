# DiscoveryConsole Codex Skill

This directory packages the Codex-facing workflow for DiscoveryConsole. The
skill teaches Codex how to act as the user's research operator: frame the
problem, design trustworthy evals, prepare knowledge and baselines, generate
agent routes, and operate CORAL as the execution engine.

The skill does not replace the CLI. It is the human/Codex/CORAL collaboration
layer.

## Install Locally

From the DiscoveryConsole repository root:

```bash
mkdir -p "$HOME/.codex/skills"
cp -a codex-skill/coral-research-workbench "$HOME/.codex/skills/"
```

Then start a new Codex session and ask it to use the `coral-research-workbench`
skill.

To check whether the execution engine is available:

```bash
python "$HOME/.codex/skills/coral-research-workbench/scripts/check_coral_install.py" --json
```

## What The Skill Provides

- A Codex/user/CORAL interaction protocol.
- Research framing gates before CORAL launch.
- Eval design guidance for breakthrough metrics, guardrails, anti-cheating,
  overfitting, profiles, and score credibility.
- Workspace, knowledge, baseline, agent-plan, and control-panel contracts.
- Scripts for availability checks, knowledge skeletons, agent/island plan
  briefs, baseline records, and eval progress.
- A two-loop workflow where CORAL performs inner search and the user plus Codex
  perform outer review, steering, knowledge promotion, and timestamp forking.

## Syncing During Development

If you edit the installed skill first, mirror it back into the repository before
publishing:

```bash
rsync -a --delete --exclude '__pycache__/' \
  "$HOME/.codex/skills/coral-research-workbench/" \
  codex-skill/coral-research-workbench/
```
