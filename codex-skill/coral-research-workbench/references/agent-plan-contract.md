# Agent Plan Contract

Use this reference before changing agent initialization, island topology, or migration behavior.

## Ownership

Codex owns the concrete agent plan. The user should inspect the plan and request regeneration if it looks wrong, but should not normally hand-edit individual seed briefs in the dashboard.

## Single-Island Mode

Single-island mode means multiple agents search the same shared problem space and can benefit from shared attempts, notes, skills, and heartbeat summaries.

Use it when:

- the problem is narrow,
- there is one dominant eval,
- method families are not clearly separable,
- or the budget is small.

Still differentiate the agents. Do not spawn five agents with the same starting method.

## Multi-Island Mode

Multi-island mode separates exploration into semi-independent groups. Each island should have a distinct theme or method family. Migration copies promising results or knowledge between islands at controlled intervals.

Use it when:

- there are multiple plausible method families,
- the search space is broad,
- premature convergence is a risk,
- or the user wants parallel research cultures.

A multi-island run needs at least one planned agent per island. Never configure more islands than agents.

## Agent Briefs

Store agent seed briefs under:

```text
knowledge/briefs/agent-seeds/
```

Each brief should include:

- title
- island ID when applicable
- starting hypothesis or technical direction
- what to inspect first
- what to try first
- what to avoid
- expected eval profile
- any guardrail concern

Keep briefs short enough to be injected into the agent context without becoming a paper.

## Island Themes

Store island themes under:

```text
knowledge/briefs/islands/
```

Each theme should state:

- method family or search posture
- what kind of attempts the island should favor
- what evidence would count as progress
- what risks the island should watch for

This is not roleplay. Avoid generic personas such as "researcher" or "engineer" unless the role changes the technical search strategy.

## Migration

Treat migration as copy/inspiration, not ownership transfer. A migrated result should let another island inspect, adapt, or challenge it while the source island remains intact.

Typical policy:

- migrate only after enough evals exist to rank attempts,
- prefer strong recent attempts,
- cap migration per cycle,
- notify the destination island so agents know what changed.

Use migration to prevent isolation, not to force all islands into one method too early.

## Regeneration

Regenerate the plan before launch when:

- agents overlap too much,
- an island has no clear theme,
- a route ignores important knowledge,
- the eval suggests a different decomposition,
- or the user rejects the proposed search strategy.

After attempts exist, changing routes is a new experimental condition. Record it through next-resume instruction or fork a new timestamp when the change is substantial.
