# Extend JBI for Bidirectional BMO <-> Jira Sync (vs. Building Elsewhere)

- Status: Accepted (transport superseded by ADR 005)
- Date: 2026-08-25

> **Amended 2026-09-16.** The decision to extend JBI rather than build the
> reverse path elsewhere stands. The *transport* described below -- events
> arriving directly over HTTP -- is superseded by
> [ADR 005](005-pubsub-transport.md): both Bugzilla and Jira events now
> arrive via Pub/Sub push, behind a single ingest seam.

Tracking issue: (see `docs/bmo-jira-bidirectional-integration-plan.md`, based on
the "BMO - Jira Integration" PRD, DRAFT v2)

## Context and Problem Statement

JBI today syncs Bugzilla (BMO) bugs to Jira issues in one direction only: a
webhook on `/bugzilla_webhook` feeds an `Executor` that runs configured step
functions, all of which write to Jira. The only value ever written back to BMO
is the `see_also` link to the created Jira issue.

The PRD asks for this to become bidirectional: field changes made in Jira
(status, assignee, priority, summary, comments) should flow back to the linked
Bugzilla bug, in addition to metabug/epic hierarchy modeling, identity
matching, and visibility enforcement in later phases.

Where should this reverse capability live: as new functionality inside JBI, as
new functionality inside BMO/Bugzilla, or as a new, separate service?

## Decision Drivers

- JBI is in active production use for many teams; the chosen approach must not
  regress the existing Bugzilla -> Jira path ("extend, don't break").
- Avoid introducing new infrastructure (datastores, services) unless the
  problem genuinely requires it.
- Correlation, auth, and retry/dead-letter handling for inbound webhooks
  already exist in JBI for the forward direction and are directly reusable for
  a reverse direction.
- The two invariants that must hold regardless of approach: never create a
  duplicate Jira issue for a bug that's already linked (Invariant A), and a
  Jira-side event must never cause a new Bugzilla bug to be filed
  (Invariant B).

## Considered Options

1. Extend JBI with a second, symmetric inbound webhook path (`/jira_webhook`)
   and a set of reverse step functions that write to Bugzilla.
2. Extend Bugzilla/BMO itself to poll or subscribe to Jira changes and write
   them back directly, bypassing JBI.
3. Build a new, separate service dedicated to the reverse (Jira -> BMO) sync
   direction, integrated with JBI and/or BMO over an API.

## Decision Outcome

Chosen option: **Option 1, extend JBI**, because it reuses infrastructure JBI
already has and does not require standing up anything new. JBI is already a
webhook receiver with an existing auth scheme (`api_key_auth`), a dead-letter
queue (`jbi/queue.py`) for retries, and an `Executor`/step-function pattern
(`jbi/runner.py`, `jbi/steps.py`) that composes behavior from configuration.
The reverse direction can reuse all of this by adding a new, isolated endpoint
and a parallel set of reverse steps, while leaving `/bugzilla_webhook` and its
code path completely untouched.

This also keeps the bug<->issue correlation logic (and therefore Invariants A
and B) in one place: the same `see_also`/remote-link correlation JBI already
performs is used to resolve an inbound Jira event back to its Bugzilla bug, or
to ignore the event outright if no such bug exists.

### Positive Consequences

- No new datastore, service, or deployment surface; only new, config-gated,
  default-off code paths in an existing service.
- Every deliverable is additive and independently reviewable/revertible (a
  config flip), since the forward path is never modified.
- Correlation, auth, retry, and instrumentation are reused rather than
  reimplemented, reducing the number of places Invariants A and B must be
  independently enforced.

### Negative Consequences

- JBI's scope grows to include logic that writes to Bugzilla, not just Jira,
  which the codebase was not originally organized around (mitigated by
  isolating this in new modules: `jbi/jira_inbound/`, `jbi/jira_steps.py`,
  `jbi/identity.py`, `jbi/visibility.py`).
- JBI remains a single point of failure/bottleneck for both sync directions
  (already true today for the forward direction; not a new risk introduced by
  this decision).

## Pros and Cons of the Options

### Option 1 - Extend JBI

- Good, because it reuses existing auth, dead-letter queue, and executor
  patterns.
- Good, because the forward path is provably untouched (new endpoint, new
  modules).
- Good, because correlation/loop-prevention logic lives in one service instead
  of being duplicated across two.
- Bad, because JBI's responsibilities grow beyond "sync to Jira" to include
  "sync from Jira".

### Option 2 - Extend Bugzilla/BMO

- Good, because BMO is the system of record for bug data, so a BMO-native
  writer has direct access to it.
- Bad, because it requires new infrastructure in BMO for something JBI already
  does for the forward direction (webhook receiving, retry/dead-letter
  handling), duplicating work.
- Bad, because correlation and loop-prevention logic would need to be
  reimplemented and kept in sync with JBI's existing forward-direction logic,
  or the two systems would need to coordinate over an API anyway.

### Option 3 - New separate service

- Good, because it isolates the new capability from JBI's existing code
  entirely.
- Bad, because it duplicates JBI's existing webhook auth, retry, and
  correlation infrastructure in a new service that has to be built, deployed,
  and operated.
- Bad, because splitting "sync to Jira" and "sync from Jira" across two
  services makes the loop-prevention story (which depends on both directions
  agreeing on what counts as an echo) harder to reason about and test.

## Links

- Engineering plan: `docs/bmo-jira-bidirectional-integration-plan.md`
