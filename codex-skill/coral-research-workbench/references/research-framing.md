# Research Framing

Use this reference before preparing a CORAL workspace from a vague or partially specified research idea.

## Output

Produce a short research frame before launch:

```text
Objective:
Expected artifact:
Current baseline:
Known bottleneck:
Breakthrough target:
Guardrails:
Anti-cheating / overfitting risks:
Eval profiles:
Required knowledge:
Agent route strategy:
Evidence needed for a claim:
```

This frame is not a paper proposal. It is the minimum structure needed to make CORAL search meaningful.

## Readiness Questions

Ask and answer:

1. What exactly should improve?
2. What must not get worse?
3. What baseline proves the task is nontrivial?
4. What failure would make a high score untrustworthy?
5. What data, repo, paper, tool, or domain context must agents see before they start?
6. What final claim would the user want to make if the run succeeds?
7. What evidence would still be needed after the best quick-eval attempt?

If the answers are weak, keep framing. Do not start CORAL.

## Baseline Standard

Before launch, Codex should either:

- reproduce an existing baseline,
- implement a simple seed baseline,
- import a known reference implementation, or
- clearly document why no baseline exists and what proxy will be used.

Record the baseline attempt in the timestamp so dashboard comparisons have a visible reference line.

## Knowledge Standard

Startup knowledge should include what agents need to avoid rediscovering basics:

- relevant papers and method summaries
- reusable open-source projects
- dataset or benchmark documentation
- known constraints and failure modes
- toolchain setup notes
- user's prior notes and preferences

Put these under `knowledge/`, register them in `manifest.jsonl`, and summarize them in `index.md` or notes. Use `inbox/` for unreviewed material found during or after a run.

## Agent Route Standard

Agent routes should be meaningfully different, not random restatements of the same plan.

Good differentiation axes:

- conservative improvement vs high-risk redesign
- theory-driven vs empirical search
- speed/latency optimization vs accuracy optimization
- guardrail-first validation vs breakthrough-first exploration
- different algorithm families
- different data representations or loss functions

For multi-island runs, islands should separate research cultures or method families. Agents within one island can share a theme but still need distinct starting tactics.

## When to Stop Framing

Move to workspace preparation when:

- the problem can be expressed as a task spec,
- at least one baseline can be scored,
- the eval has a plausible trust argument,
- necessary knowledge can be indexed,
- agent routes can be differentiated,
- and the user understands what the first CORAL run is meant to test.
