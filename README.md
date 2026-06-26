<div align="center">

# DiscoveryConsole

### Human-in-the-loop control console for open-ended research search.

[![Apache 2.0 License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg?logo=python&logoColor=white)](https://python.org)
[![Built on CORAL](https://img.shields.io/badge/Built%20on-CORAL-2F6F73.svg)](https://github.com/Human-Agent-Society/CORAL)
[![Codex workflow](https://img.shields.io/badge/Codex-research%20workflow-111827.svg)](codex-skill/README.md)

**English** | [中文](README_CN.md)

</div>

<p align="center">
<a href="#what-it-is">What It Is</a> · <a href="#why-it-exists">Why It Exists</a> · <a href="#quick-start">Quick Start</a> · <a href="#architecture">Architecture</a> · <a href="#relationship-to-coral">Built on CORAL</a>
</p>

**DiscoveryConsole** is a research cockpit for Codex power users. It helps a human researcher run open-ended research loops: Codex prepares the workspace, CORAL executes multiple agent routes, graders score attempts, the dashboard shows state, and the human steers the next search cycle.

This project is for researchers who already use coding agents heavily and want a more disciplined loop around ideas, evals, baselines, evidence, and feedback.

![DiscoveryConsole dashboard preview](assets/demo.gif)

## What It Is

DiscoveryConsole combines three pieces into one workflow:

- **A research control console** for launching, pausing, resuming, and inspecting multi-agent runs.
- **A Codex skill workflow** that teaches Codex how to prepare knowledge, evals, baselines, and agent route plans before launch.
- **A CORAL-based execution engine** that runs isolated coding agents, evaluates commits, shares state, and records attempts.

The intended loop is:

```text
Frame the research task
→ prepare knowledge and eval criteria with Codex
→ record baselines
→ launch diverse agent routes
→ monitor scores, queue, logs, and knowledge
→ pause and inject human feedback
→ review evidence and possible reward hacking
→ start the next timestamped search round
```

## Why It Exists

Modern coding agents are strong at implementation, but research work fails when the surrounding loop is weak:

- evals are underspecified or easy to game;
- baselines and score versions are not frozen;
- agent runs produce scattered logs instead of durable knowledge;
- multiple attempts repeat the same direction;
- human feedback is lost between runs;
- a high score is treated as truth without guardrails.

DiscoveryConsole treats **score credibility** as part of the product. A run is not just agent output; it is a timestamped experiment site with a grader, eval profile, baseline evidence, knowledge sources, agent briefs, attempts, logs, and review notes.

## Who It Is For

DiscoveryConsole is aimed at:

- AI researchers and PhD students using Codex or Claude Code daily;
- algorithm engineers running many experimental variants;
- independent researchers who need repeatable research loops;
- teams exploring open-ended optimization, model, systems, or scientific tasks.

It is not a one-click paper generator. The human remains in the loop as the evaluator, reviewer, and decision maker.

## Quick Start

### 1. Install the CLI from this repository

```bash
curl -fsSL https://raw.githubusercontent.com/PullMyBoots/DiscoveryConsole/main/install.sh | sh
```

The CLI command is still `coral` because DiscoveryConsole currently ships as a CORAL-derived execution engine.

```bash
coral --help
```

### 2. Install the Codex skill

Clone this repository, then copy the skill into your Codex skills directory:

```bash
git clone https://github.com/PullMyBoots/DiscoveryConsole.git
cd DiscoveryConsole
mkdir -p "$HOME/.codex/skills"
cp -a codex-skill/coral-research-workbench "$HOME/.codex/skills/"
```

Start a new Codex session and ask it to use the `coral-research-workbench` skill to prepare a DiscoveryConsole/CORAL workspace.

### 3. Create or prepare a task

For a minimal scaffold:

```bash
coral init my-task
cd my-task
coral validate .
```

In the intended workflow, Codex prepares the task more fully before launch:

- `knowledge/` sources, notes, and eval specification;
- packaged grader and eval profiles;
- baseline attempt records;
- runnable agent initialization bundles: route plans and first-eval scripts;
- resource and runtime configuration.

Then Codex materializes the run without launching agents:

```bash
coral prepare -c task.yaml
coral validate --run-dir results/<task>/<timestamp>/.coral
```

After readiness passes, launch the prepared timestamp:

```bash
coral start -c results/<task>/<timestamp>/.coral/config.yaml
```

### 4. Open the dashboard

```bash
coral ui
```

Use the dashboard to inspect readiness, tune high-level controls, launch the run, pause/resume, review attempts, and capture knowledge for the next cycle.

## Architecture

```text
Human researcher
  ↕
DiscoveryConsole dashboard
  ↕
Codex research-workbench skill
  ↕
CORAL execution engine
  ├─ isolated agent worktrees
  ├─ grader daemon and eval queue
  ├─ timestamped run directories
  ├─ public knowledge and attempts
  └─ shared public knowledge and attempts
```

Important concepts:

- **Timestamped runs**: every launch creates a frozen experiment site.
- **Prepare/start split**: `coral prepare` creates the timestamp, repo clone, shared state, and agent worktrees; `coral start` only launches a prepared timestamp.
- **Knowledge base**: startup sources and runtime notes live under `.coral/public/knowledge/`.
- **Eval profiles**: quick/medium/full/stress profiles let you separate iteration speed from final evidence.
- **Agent initialization bundles**: Codex prepares differentiated runnable starting routes plus first-eval scripts instead of letting every agent begin identically.
- **Human feedback**: paused runs can receive a next-resume instruction that all agents hear on resume.
- **Review**: scores, baselines, eval versions, guardrails, and possible cheating are reviewed together.

## Supported Agent Backends

The control console focuses on the agent backends most relevant to this workflow:

| Backend | Runtime value | Notes |
| --- | --- | --- |
| Codex | `codex` | Supports per-agent model and reasoning effort. |
| Claude Code | `claude_code` | Supports per-agent model and effort. |
| OpenCode | `opencode` | Supports provider/model and provider-specific variants. |

Each backend must be installed and authenticated separately on the machine that runs the agents.

## Codex Skill

The bundled skill is in [codex-skill/coral-research-workbench](codex-skill/coral-research-workbench/). It does not replace the CLI. It tells Codex how to prepare a high-quality workspace before the human launches the run.

It includes scripts for:

- checking whether the CORAL/DiscoveryConsole engine is installed;
- preparing a knowledge skeleton;
- writing eval progress;
- recording baseline attempts;
- generating agent route briefs.

See [codex-skill/README.md](codex-skill/README.md).

## Relationship To CORAL

DiscoveryConsole is built on top of [CORAL](https://github.com/Human-Agent-Society/CORAL), an Apache-2.0 open-source project for autonomous multi-agent coding and self-evolution.

This repository contains a modified CORAL engine plus a Codex-native research console and skill workflow. The original CORAL paper and project remain the foundation of the execution model: isolated agent worktrees, shared state, grader daemon, eval queueing, and multi-agent evolution.

If you use this project in research, cite CORAL as the underlying engine:

```bibtex
@article{qu2026coral,
  title={CORAL: Towards Autonomous Multi-Agent Evolution for Open-Ended Discovery},
  author={Qu, Ao and Zheng, Han and Zhou, Zijian and Yan, Yihao and Tang, Yihong and Ong, Shao Yong and Hong, Fenglu and Zhou, Kaichen and Jiang, Chonghe and Kong, Minwei and Zhu, Jiacheng and Jiang, Xuan and Li, Sirui and Wu, Cathy and Low, Bryan Kian Hsiang and Zhao, Jinhua and Liang, Paul Pu},
  journal={arXiv preprint arXiv:2604.01658},
  year={2026}
}
```

## Status

DiscoveryConsole is an early preview for first users. Expect rough edges in docs, packaging, and workflow ergonomics. The current priority is to validate the human-in-the-loop research loop with real Codex/Claude Code power users before stabilizing the public API.

## Development

```bash
uv sync --extra dev
uv run pytest -q
uv run ruff check .

cd web
npm install
npm run lint
npm run build
```

## License

DiscoveryConsole is released under the Apache 2.0 [LICENSE](LICENSE). Because it is built on CORAL, original CORAL attribution and license terms are preserved.
