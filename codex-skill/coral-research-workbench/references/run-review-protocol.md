# Run Review Protocol

Use this reference after a CORAL run stops or pauses and the user wants to understand what happened or decide the next move.

## Purpose

The review loop turns raw CORAL activity into scientific judgment. The goal is not to celebrate the best score; it is to decide whether the result is credible, useful, and worth promoting.

## Evidence to Inspect

Start with `/api/review` or the Knowledge dashboard Review panel. Then inspect:

- best attempts and their score components
- baseline delta
- failed and pending eval jobs
- eval version/profile identity
- changes to `eval_spec.md`
- new notes and sources
- agent logs only when the dashboard summary is insufficient
- resource and cost signals when relevant

## Review Questions

Ask:

1. Did the best attempt improve the thing the user cares about?
2. Did it preserve guardrails?
3. Could the score be reward hacking, leakage, overfitting, or benchmark luck?
4. Did multiple agents independently converge on the result, or is it isolated?
5. Is the quick-eval ranking likely to survive full/stress eval?
6. Did any new source or note change the research framing?
7. Should the next step be continue, pivot, validate, rewrite eval, or stop?

## Knowledge Promotion

Classify external sources as active or archived. Keep useful references active
with `coral kb add external`; archive stale references with `coral kb remove`.

Durable conclusions should become practice knowledge through `coral kb note` or
`coral kb archive --attempt <hash>`.

## Resume Instructions

When the user wants to steer the next continuation of the same timestamp, save the instruction to:

```text
.coral/public/control/next_instruction.md
```

Good resume instructions are concise and operational:

- what to emphasize,
- what to stop doing,
- what evidence to check,
- what source or attempt to reuse,
- what guardrail to protect.

Do not use resume instructions to hide an eval change. If the scoring meaning changes, fork.

## Targeted Adjustments

Use the smallest adjustment that matches the user's feedback:

- Run-level steering: write `.coral/public/control/next_instruction.md`.
- Agent-level critique: reset that agent's notebook with
  `coral kb notebook --agent <agent-id> --set <file> --reason external-adjustment --by codex`.
- External knowledge changes: use `coral kb add external ...` or
  `coral kb remove <src-id>`.
- Eval meaning changes, major route rewrites, or baseline changes: fork a new
  timestamp instead of mutating the current evidence.

## Forking a New Timestamp

Fork when:

- eval semantics change,
- important knowledge has been promoted,
- the agent plan changes substantially,
- the baseline changes,
- or old attempts should no longer be interpreted under the new setup.

A new timestamp should copy config, snapshots, accepted knowledge, and prepared routes as appropriate, but should not copy old attempts as if they were scored in the new condition.

## Acceptance

A result is ready to present as a claim only when:

- it beats the baseline under the relevant eval,
- guardrails pass,
- anti-cheating and overfitting risks have been addressed,
- eval identity and profile are recorded,
- the result can be reproduced,
- and the user agrees the evidence supports the intended claim.

Otherwise, describe it as a promising candidate, not as a solved research result.
