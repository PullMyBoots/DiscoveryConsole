# Eval Design

Use this reference when designing the evaluation philosophy for open-ended research search.

## Core Principle

The scalar score is not just a number. It is the argument that a discovered result is better. CORAL's scheduler optimizes this scalar, so the scalar must be designed to resist misleading progress.

## Evaluation Level And Evidence Ladder

Choose L1/L2/L3 by asking what environment the result must work in. The
evaluation level is a research-design decision, not a runtime knob.

- L1 fits very fixed scenarios: the goal is to optimize a known component,
  program step, script, kernel, or benchmark path under an open scoring
  contract. There is little room for a hidden generalization claim because the
  task is defined by the fixed contract itself.
- L2 fits fixed task families where overfitting to public probes is the main
  threat. A-space can be open for exploration, but B-space should be hidden,
  representative, and mature enough to decide ranking or acceptance.
- L3 fits open-world or deployment-uncertain claims. A/B can guide search and
  compare candidates, but they may still select a method that overfits the
  validation regime or lands in a local optimum. C-space is the sealed final
  validation closest to the real use environment.

The more certain and closed the target environment is, the more appropriate L1
becomes. The more uncertain, open, or deployment-dependent it is, the more
appropriate L3 becomes.

When generalization matters, do not treat A/B/C as arbitrary random splits from
one distribution. Design them as a continuous evidence ladder:

- A-space: cheap, open, and learnable enough for agents to optimize, but not
  so simple that it becomes disconnected from the real problem.
- B-space: hidden and more representative, used to detect overfitting to A
  while preserving enough continuity that A improvements can transfer.
- C-space: sealed and closest to the final deployment or scientific claim,
  used only for final validation outside the normal CORAL loop.

Avoid discontinuities. If A is too primitive and B is much harder, the search
loop has no reliable optimization signal. If B and C are too different, the
B-space winner may not support the final claim. The goal is a deliberate
increase in realism and difficulty, not a cliff.

## Metric Groups

Use two metric groups plus hard failures:

- Breakthrough metrics: quantify what should improve. Higher should be better.
- Guardrail metrics: quantify what must stay usable, valid, robust, or efficient. These often have minimum acceptable floors.
- Hard failures: invalidate attempts that cheat, leak data, break output format, time out, or exploit the grader.

Then define one scalar:

```text
score = f(breakthrough_metrics, guardrail_metrics, hard_failures)
```

Prefer higher-is-better. Document the formula in `knowledge/eval_spec.md`.

## Guardrails Are Not Optional

A high breakthrough score is not credible if the result:

- violates the output contract,
- collapses on important cases,
- uses hidden labels or test leakage,
- hard-codes benchmark artifacts,
- consumes unacceptable resources,
- depends on a non-reproducible environment,
- or wins only because the quick eval is too narrow.

Encode these as hard failures or strong penalties.

## Overfitting and Cheating

Overfitting is a credibility threat, not always a moral violation. Treat it as part of anti-cheating and trust design.

Use one or more:

- held-out cases
- randomized seeds
- stress tests
- distribution-shift checks
- hidden or regenerated cases
- invariant checks
- ablations against suspicious shortcuts
- re-evaluation under a full profile

If a method class is deterministic, analytic, or constrained enough that overfitting is unlikely, document why. Do not ignore the question.

## Eval Profiles

Use multiple cost tiers when useful:

- `quick`: cheap enough for frequent iteration; should predict full-eval ordering.
- `medium`: stronger signal for promising attempts.
- `full`: final validation under the main benchmark.
- `stress`: robustness, edge cases, anti-cheating, distribution shift.

Agents usually optimize under `quick`; the user and Codex validate claims with stronger profiles.

## Comparability

Never directly compare:

- attempts scored by different eval versions,
- `quick` and `full` scores as if they measure the same thing,
- attempts before and after changing the grader semantics,
- attempts that used different hidden data or different resource contracts unless recorded and normalized.

If the eval meaning changes, bump `grader.eval_version` and start a new timestamp or re-run selected attempts under one frozen eval.

## Eval Spec Checklist

`knowledge/eval_spec.md` should state:

- what the scalar score means,
- all breakthrough metrics,
- all guardrail metrics and floors,
- hard failure conditions,
- known ways agents might exploit the eval,
- how quick/full/stress profiles differ,
- what evidence is enough to accept a result,
- what evidence would require another run.
