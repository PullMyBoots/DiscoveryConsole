<div align="center">

# DiscoveryConsole

### 面向 Codex 重度用户的人机协作开放式科研控制台

[![Apache 2.0 License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg?logo=python&logoColor=white)](https://python.org)
[![Built on CORAL](https://img.shields.io/badge/Built%20on-CORAL-2F6F73.svg)](https://github.com/Human-Agent-Society/CORAL)
[![Codex workflow](https://img.shields.io/badge/Codex-research%20workflow-111827.svg)](codex-skill/README.md)

[English](README.md) | **中文**

</div>

<p align="center">
<a href="#这是什么">这是什么</a> · <a href="#为什么需要它">为什么需要它</a> · <a href="#快速开始">快速开始</a> · <a href="#系统结构">系统结构</a> · <a href="#与-coral-的关系">与 CORAL 的关系</a>
</p>

**DiscoveryConsole** 是一个科研驾驶舱：它面向 Codex / Claude Code 重度用户，把 Codex 的工作空间准备能力、CORAL 的多智能体执行能力、评测脚本、知识库和控制面板组织成一个完整的开放式科研搜索循环。

它不是“把问题丢给 agent 等答案”的工具，而是让人类研究者持续掌舵：设计评测、启动多路线探索、观察分数和证据、暂停注入反馈、审计结果可信度，并进入下一轮搜索。

![DiscoveryConsole dashboard preview](assets/demo.gif)

## 这是什么

DiscoveryConsole 把三部分组合成一个工作流：

- **科研控制台**：启动、暂停、恢复和观察多 agent 运行。
- **Codex skill 工作流**：让 Codex 在启动前准备知识库、eval、baseline、agent 技术路线。
- **基于 CORAL 的执行引擎**：隔离 agent worktree、运行 grader、记录 attempts、共享知识。

理想流程是：

```text
定义研究任务
→ Codex 准备知识库和评测标准
→ 记录 baseline
→ 启动多条 agent 探索路线
→ 观察分数、队列、日志和知识沉淀
→ 暂停并注入人类反馈
→ 审计证据和潜在 reward hacking
→ 开启下一轮 timestamp 搜索
```

## 为什么需要它

现在的 coding agent 已经很会写代码，但科研真正困难的是周围的循环：

- eval 设计不清晰，容易被刷分；
- baseline 和评测版本没有冻结；
- agent 跑完只留下零散日志，没有知识沉淀；
- 多个 agent 容易重复同一个方向；
- 人类反馈无法自然进入下一轮；
- 一个高分经常被误认为“真实进展”。

DiscoveryConsole 把**分数可信度**当成产品的一部分。一次运行不是一堆 agent 输出，而是一个 timestamped experiment site：包含 grader、eval profile、baseline、知识来源、agent brief、attempt、日志和 review note。

## 适合谁

DiscoveryConsole 适合：

- 每天大量使用 Codex / Claude Code 的 AI 研究者和博士生；
- 需要同时跑很多实验路线的算法工程师；
- 独立研究者；
- 正在探索开放式优化、模型、系统或科学问题的团队。

它不是一键论文生成器。人类仍然是评估者、审稿人和决策者。

## 快速开始

### 1. 从本仓库安装 CLI

```bash
curl -fsSL https://raw.githubusercontent.com/PullMyBoots/DiscoveryConsole/main/install.sh | sh
```

当前 CLI 命令仍然叫 `coral`，因为 DiscoveryConsole 现在是基于 CORAL 改造的执行引擎。

```bash
coral --help
```

### 2. 将 Codex skill 软链接到项目

克隆仓库，然后把配套 skill 暴露给你要运行 Codex 的项目。推荐做法是保留仓库里的源 skill，在项目目录的 `.agents/skills/` 下创建软链接：

```bash
git clone https://github.com/PullMyBoots/DiscoveryConsole.git
cd DiscoveryConsole
DISCOVERYCONSOLE_DIR="$PWD"

cd /path/to/your/research-project
mkdir -p .agents/skills
ln -sfn "$DISCOVERYCONSOLE_DIR/codex-skill/coral-research-workbench" \
  .agents/skills/coral-research-workbench
```

这个项目级软链接很重要：Codex 在该研究项目中工作时，应当能看到准确版本的 `coral-research-workbench` skill。你也可以额外把同一个 skill 软链接到 `$HOME/.agents/skills/` 做全局复用，但推荐安装步骤必须包含项目目录下的 `.agents/skills/` 软链接。

从该研究项目重新打开一个 Codex session，让 Codex 使用 `coral-research-workbench` skill 来准备 DiscoveryConsole/CORAL workspace。

### 3. 创建或准备任务

最小脚手架：

```bash
coral init my-task
cd my-task
coral validate .
```

在推荐工作流里，Codex 会在启动前进一步准备：

- `knowledge/` 资料、笔记和 eval specification；
- packaged grader 和 eval profiles；
- baseline attempt 记录；
- 可运行的 agent 初始化包：路线方案、首轮 eval 脚本和路线知识包；
- 资源和运行时间配置。

### 4. 打开控制台

```bash
coral ui
```

在 dashboard 里检查 readiness、调整高层参数、启动运行、暂停/恢复、审计 attempts，并把知识沉淀到下一轮。

## 系统结构

```text
人类研究者
  ↕
DiscoveryConsole dashboard
  ↕
Codex research-workbench skill
  ↕
CORAL execution engine
  ├─ 隔离的 agent worktrees
  ├─ grader daemon 和 eval queue
  ├─ timestamped run directories
  ├─ public knowledge 和 attempts
  └─ 共享 public knowledge 和 attempts
```

关键概念：

- **Timestamped runs**：每次启动都是一个冻结的实验现场。
- **Knowledge base**：启动前资料和运行时笔记统一放在 `.coral/public/knowledge/`。
- **Eval profiles**：quick/medium/full/stress 区分快速迭代和最终证据。
- **Agent briefs**：Codex 为不同 agent 准备有区分度的初始技术路线。
- **Human feedback**：暂停后写入 next-resume instruction，恢复时所有 agent 都能听到。
- **Review**：分数、baseline、eval version、guardrail 和潜在作弊一起审计。

## 支持的 Agent 后端

控制台聚焦当前工作流最重要的三个后端：

| Backend | Runtime value | 说明 |
| --- | --- | --- |
| Codex | `codex` | 支持 per-agent model 和 reasoning effort。 |
| Claude Code | `claude_code` | 支持 per-agent model 和 effort。 |
| OpenCode | `opencode` | 支持 provider/model 和 provider-specific variant。 |

每个后端都需要在运行机器上单独安装并认证。

## Codex Skill

配套 skill 位于 [codex-skill/coral-research-workbench](codex-skill/coral-research-workbench/)。它不是 CLI 的替代品，而是告诉 Codex 如何在启动前准备高质量 workspace。

它包含脚本用于：

- 检查 CORAL/DiscoveryConsole 执行引擎是否安装；
- 准备 knowledge skeleton；
- 写入 eval progress；
- 记录 baseline attempt；
- 生成 agent route briefs。

详见 [codex-skill/README.md](codex-skill/README.md)。

## 与 CORAL 的关系

DiscoveryConsole 基于 [CORAL](https://github.com/Human-Agent-Society/CORAL) 构建。CORAL 是 Apache-2.0 开源的自主多智能体编程和自我进化框架。

本仓库包含经过改造的 CORAL 执行引擎，以及面向 Codex 的科研控制台和 skill 工作流。原始 CORAL 论文和项目仍然是执行模型的基础：隔离 worktree、共享状态、grader daemon、eval 队列、多 agent evolution。

如果你在研究中使用本项目，请引用底层 CORAL 引擎：

```bibtex
@article{qu2026coral,
  title={CORAL: Towards Autonomous Multi-Agent Evolution for Open-Ended Discovery},
  author={Qu, Ao and Zheng, Han and Zhou, Zijian and Yan, Yihao and Tang, Yihong and Ong, Shao Yong and Hong, Fenglu and Zhou, Kaichen and Jiang, Chonghe and Kong, Minwei and Zhu, Jiacheng and Jiang, Xuan and Li, Sirui and Wu, Cathy and Low, Bryan Kian Hsiang and Zhao, Jinhua and Liang, Paul Pu},
  journal={arXiv preprint arXiv:2604.01658},
  year={2026}
}
```

## 当前状态

DiscoveryConsole 目前是面向第一批用户的 preview。文档、打包和工作流交互还会继续变化。当前重点是和真实 Codex/Claude Code 重度用户一起验证 human-in-the-loop 科研搜索循环，而不是承诺稳定 API。

## 开发

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

DiscoveryConsole 使用 Apache 2.0 [LICENSE](LICENSE)。由于本项目基于 CORAL，原始 CORAL 的 attribution 和 license terms 会被保留。
