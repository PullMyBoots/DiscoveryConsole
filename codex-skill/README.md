# CORAL Codex Skill

This directory packages the Codex-facing workflow for the CORAL research
workbench. Keep it in the repository so users can install the skill while still
using the full CORAL project as the execution engine.

## Install Locally

From the CORAL repository root:

```bash
mkdir -p "$HOME/.codex/skills"
cp -a codex-skill/coral-research-workbench "$HOME/.codex/skills/"
```

Then start a new Codex session and ask it to use the `coral-research-workbench`
skill.

The skill does not replace the CORAL CLI. To check whether the execution engine
is available:

```bash
python "$HOME/.codex/skills/coral-research-workbench/scripts/check_coral_install.py" --json
```

## What The Skill Provides

- The Codex/CORAL operating contract.
- Workspace, eval, and control-panel boundaries.
- Scripts for CORAL availability checks, knowledge skeletons, agent/island plan
  briefs, baseline records, and eval progress.
- A repeatable flow where Codex prepares the timestamp workspace and the user
  launches or adjusts the run from the CORAL control panel.

## Syncing During Development

If you edit the installed skill first, mirror it back into the repository before
publishing:

```bash
rsync -a --delete --exclude '__pycache__/' \
  "$HOME/.codex/skills/coral-research-workbench/" \
  codex-skill/coral-research-workbench/
```
