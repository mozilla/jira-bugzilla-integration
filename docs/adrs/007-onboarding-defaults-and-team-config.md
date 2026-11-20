# Onboard Teams by Config, With Defaults On and Team-Owned Mapping Files

- Status: Accepted
- Date: 2026-11-20

Amends the bidirectional sync plan (`docs/bmo-jira-bidirectional-integration-plan.md`)
§1 "Pilot", §6 "default-OFF", and §10.1 "mirror-epic lifecycle". Records
decisions made while the pilot was running, so the plan's original choices
are not mistaken for the shipped behavior.

## Context and Problem Statement

The plan scoped the pilot to one Product::Component (Core :: Machine
Learning: On Device), made every new behavior default-OFF per action, and
created metabug Epics lazily. Running it on stage showed three problems:

- Teams already map to Jira through a **whiteboard tag → project** action.
  A Product::Component scope was a second, competing notion of "which bugs
  are ours".
- Default-OFF per action meant every team needed per-team YAML before it
  could test anything. In practice, stage teams had features silently off
  (no review moves, comment copying on for 2 of 6 teams).
- Lazy Epics only existed for metabugs whose children happened to sync. A
  team's planning view was missing its own trackers.

## Decision

1. **Onboarding is one flag on the team's existing action**,
   `jira_inbound_enabled: true`. Product::Component scope
   (`sync_products_components`) stays as an optional filter. It is not an
   onboarding mechanism. The On Device pilot was dropped.
2. **Onboarded actions get everything on by default:** review moves
   ("In Review" / "In Progress"), the `jbi-sync-stop` label, Jira → BMO
   comment copying, metabug Epics, and release-data labels. The steps these
   need are appended to the action's lists, including custom ones. Actions
   that are not onboarded are unchanged, and that is what keeps production
   safe: no prod action is onboarded.
3. **Teams own their mappings** in `config/projects/<JIRA_KEY>.yaml`, added
   by PR. Precedence: project file, then action, then defaults. Maps replace
   rather than merge. The file can switch features **off**, including
   comment copying, but cannot onboard a team, change steps, or turn comment
   copying on: those decide whether JBI touches a team's data at all, and
   stay in the reviewed main config. Unknown keys fail CI.
4. **Every tagged metabug is an Epic, eagerly, even when empty.** A metabug
   without the tag gets one on its first synced child. Jira cannot parent an
   Epic under an Epic, so nested metabugs become "blocks" links between their
   Epics, consistent with §10.1's "membership as links, parent as planning
   overlay". `bin/ensure_metabug_epics.py` backfills by tag.

## Consequences

- A team is usable on stage the moment its action is onboarded. A workflow
  with different status names is a one-file PR, not a ticket.
- Defaults that don't match a project degrade safely: a missing status is a
  logged skip; a missing transition is never forced.
- "On by default" now means "on for every onboarded team", so turning
  production on is a per-team decision, made one action at a time.
- Eager Epics mean more Epics in Jira, including empty ones. Accepted:
  a tracker that exists in Bugzilla should be visible in planning.
