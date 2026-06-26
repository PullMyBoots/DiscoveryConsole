# Agent Plan Contract

Use this reference before changing agent initialization or route planning behavior.

## Ownership

Codex owns the concrete agent initialization bundle. The user should inspect it
and request regeneration if it looks wrong, but should not normally hand-edit
individual initialization plans or first-eval scripts in the dashboard.

## Route Plan

CORAL runs multiple agents against one shared public state space. Diversity
comes from initial technical routes and the eval feedback they produce.

Use multiple routes when:

- there are several plausible method families,
- premature convergence is a risk,
- the eval has meaningful component metrics,
- or the user wants broad technical exploration.

Still differentiate the agents. Do not spawn several agents with the same
starting method.

## Agent Initialization Bundles

Store runnable agent initialization plans and first-eval scripts under:

```text
knowledge/briefs/agent-seeds/
```

Each agent must have:

- `knowledge/briefs/agent-seeds/<agent-id>.md`
- `knowledge/briefs/agent-seeds/<agent-id>.eval.sh`

Each markdown plan should include:

- title
- starting hypothesis or technical direction
- knowledge lookup instructions using `coral kb index ...`
- what to try first as a runnable implementation or diagnostic
- the path to the first-eval script
- what to avoid
- expected eval profile
- any guardrail concern
- how the agent should evolve the route from eval feedback

Keep plans short enough to be injected into the agent context without becoming a
paper. They are starting technical plans.

Each first-eval script must be executable and must submit an official CORAL eval,
normally by calling `coral eval -m "<message>"`. It should not edit code itself.
Its job is to give the agent a concrete launch rail: apply the first
route-specific change, then run the script to get score evidence.

## Knowledge Access

Agents access knowledge through:

- `coral kb index manual`
- `coral kb index external`
- `coral kb index practice --by score|route|agent|metric`
- `coral kb read <id>`
- `coral kb note "..."`
- `coral kb archive --attempt <hash>`

If a route needs substantial background, register the reference as external
knowledge and cite the specific `src-*` id in the agent plan.

## Regeneration

Regenerate the plan before launch when:

- agents overlap too much,
- a route ignores important knowledge,
- the eval suggests a different decomposition,
- or the user rejects the proposed search strategy.

After attempts exist, changing routes is a new experimental condition. Record it
through next-resume instruction or fork a new timestamp when the change is
substantial.
