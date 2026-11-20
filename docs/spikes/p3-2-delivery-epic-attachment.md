# Spike P3-2: putting newly filed bugs into a delivery epic

- Status: Findings, no code. A decision is needed before any implementation.
- Date: 2026-11-20
- Plan: `docs/bmo-jira-bidirectional-integration-plan.md` §10 (P3-2), PRD §4.2

## The question

When a bug is filed in Bugzilla and JBI creates its Jira issue, can that issue
land in the right **delivery epic** (the Epic a team is working toward this
quarter) without a person triaging it by hand?

Phase 2 already parents an issue under its **metabug's** Epic. That covers
bugs filed against a tracker. It doesn't cover the common case of a bug
that blocks nothing, which lands in Jira unparented.

## Constraints that shape the answer

- **Jira owns planning.** Plan §4 and R-07: the parent is a planning
  decision. JBI may seed it once, at creation, and must never move it later.
- **A wrong parent is worse than none.** It puts work in someone else's
  plan, and nobody notices until the report is wrong. An unparented issue
  shows up in triage. Anything that guesses has to be very accurate or not
  attempted.
- **No per-team Jira configuration.** Onboarding must stay a config change
  (plan §12).
- **Bugzilla has no field for "delivery epic".** Whatever signal there is
  comes from the component, the whiteboard, or keywords.

## Options

| | Mechanism | Signal | Fits the constraints? | Cost |
|---|---|---|---|---|
| A | `default_parent_epic` in the team's project file: one Epic key, the team's triage or current-quarter epic | none needed | Yes. Deterministic and team-owned. Seeded once at creation; a metabug parent wins. | Small: one setting, applied in `maybe_seed_epic_parent` |
| B | `component_epics` map in the project file: Bugzilla component → Epic key | bug component | Mostly. Deterministic, but the map goes stale as epics close each quarter. | Small to medium, plus quarterly upkeep by the team |
| C | Whiteboard suffix `[tag-EPIC-123]` names the parent | person filing | Yes, but it depends on the filer knowing the epic key, and few will. | Small |
| D | A team's own Jira Automation rule: "issue created with label `bugzilla` and component X → set parent" | anything Jira knows | Planning stays in Jira, but it's per-team Jira config, which onboarding avoids. | No JBI code |
| E | Inference: text similarity or a model choosing among open epics | summary/description | **No.** It guesses, and a confident wrong parent is the worst outcome. | Large |

## Recommendation

1. **Build A** as an opt-in project-file setting. It gives every team a
   reliable "lands somewhere visible" without guessing, and it's the same
   seed-once code path as the metabug parent. If an Epic is closed or missing,
   JBI leaves the issue unparented and logs a warning. It never picks
   another epic.
2. **Offer B only if a pilot team asks for it**, with the reconciliation
   report flagging map entries that point at closed epics, so the staleness
   is visible.
3. **Document D** as the escape hatch for teams that want rich, Jira-side
   rules. It's their planning tool, and nothing in JBI has to change.
4. **Reject E.**

Precedence when several apply: the metabug's Epic (Phase 2), then C, then B,
then A. Seeded once, at creation, and never re-parented (R-07).

## Open questions for the PRD owner

- Is "a visible triage epic" (A) good enough for §4.2? Or does §4.2 mean the
  *correct delivery* epic, which only B, C or D can give?
- Who maintains a component → epic map (B) as quarters turn over?
