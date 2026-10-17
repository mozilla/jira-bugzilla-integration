# BMO ↔ Jira Bidirectional Integration — Engineering Plan

> **Status:** DRAFT for engineering review.
> **Purpose:** Translate the "BMO – Jira Integration" PRD (v2) into a concrete,
> reviewable engineering plan: what exists in JBI today, what the requirements
> ask for, where the two diverge, and a phased, testable path to close the gap.
> **Audience:** Reviewers and implementers. This document does not itself change
> behavior; it is the design and traceability reference the implementation PRs
> will point back to.
> **Revision:** v5 — the transport is **Pub/Sub pull** (a streaming-pull
> consumer process), correcting v4's push subscription; a Jira label can halt
> syncing for a pair. See "What changed in v4". v3 incorporated the PR #1386 review
> feedback, indexed in "Review feedback — what changed in v3".

---

## 0. Review feedback — what changed in v3

Three issues were raised in review of v2. Each is resolved in the body of the
plan; this table is the index so a returning reviewer can go straight to the
change.

| # | Review finding | Resolution | Where |
|---|---|---|---|
| 1 | Per-project Jira Automation rules don't scale — prod config already spans 35 Jira projects across 43 actions, and each opt-in would need a hand-built rule in that project with no central visibility. | Adopted: **one centrally-owned multi-project Automation rule** scoped by JQL. Onboarding a project becomes a one-line JQL edit. Per-project rules are kept only as the documented fallback if the site is not on a plan that supports multi-project rules. | §1, §12 |
| 2 | The Bugzilla→Jira `status_map` is many-to-one and therefore not invertible; a literal reverse map would be N hand-maintained maps. | Adopted: **never invert `status_map`.** Reverse *status* derives from Jira's project-agnostic `statusCategory`; reverse *resolution* derives from the inverted per-action `resolution_map` — which, unlike `status_map`, is injective in all 17 prod configs that define one. Residual ambiguity is handled explicitly rather than guessed. | §1, §4.1 |
| 3 | Loop prevention only covered the Jira→BMO direction; a reverse write into BMO fires the normal Bugzilla webhook and re-enters the forward pipeline. | Adopted: echo suppression is now **symmetric** and stated as Invariant C. The forward path gains the same "was this us?" actor check using `WebhookEvent.user.login`, and D7's read-before-write idempotency is the second line of defence. | Invariant C, §7, §9-D6b |

---

## 0.1 What changed in v4

Requested by the repo admin, and implemented before Phase 2 continues.

| Change | Why it matters here |
|---|---|
| **Transport is Pub/Sub pull** (v5, correcting v4's push), for *both* Bugzilla and Jira events, replacing the direct Automation web request and the BMO webhook as the primary path. | Supersedes the §1 "Push over polling" decision and rewrites §7 and §12. Retries, backoff and dead-lettering become subscription configuration; the broker's dead-letter topic replaces the file-based dead-letter queue, which resolves two v3 limitations (the queue could not hold Jira events, and it assumed a single instance). A consumer process is a second deployment unit, with flow control, ordering keys and a bounded pull window to operate. |
| **One ingest seam** (`jbi/ingest.py`) that every transport calls. | Each transport previously wired itself into the core differently. The seam returns an *acknowledgement decision*, which HTTP never needed but a broker requires. |
| **Duplicate suppression** on the broker message id. | At-least-once delivery makes duplicates normal. Most of the pipeline is idempotent already, but a redelivered comment event would post twice. |
| **A sync-stop label** halts a pair in both directions (new Invariant E). | Gives humans an escape hatch that outranks every other rule, without a deploy or a config change. |

## 1. Decisions locked

These decisions were settled with stakeholders and constrain everything below.
They are stated with their reasoning so reviewers can challenge the *reasoning*
rather than infer it.

| Area | Decision |
|---|---|
| Transport (v5) | **Pub/Sub pull**: a streaming-pull consumer (`python -m jbi consume`) running as its own process beside the web service. Retry/backoff/dead-letter are subscription config. The Automation rule still *originates* Jira events but publishes to a topic rather than calling JBI directly; `/jira_webhook` and `/bugzilla_webhook` remain for compatibility and local testing. |
| Event injection (v4) | **One seam**, `jbi.ingest.ingest_event(InboundEvent) -> IngestResult`, called by every transport. Its outcome (`HANDLED`/`IGNORED`/`RETRY`/`PERMANENT_FAILURE`) is the ack decision; `IGNORED` **acks**, because an event JBI does not act on is normal traffic. |
| Inbound Jira rule | **One centrally-owned, multi-project** Automation rule scoped by JQL (not one per project — see §12), now publishing to the topic. |
| Reverse status/resolution | **Never invert `status_map`** — it is many-to-one. Reverse *status* is derived from Jira's `statusCategory`; reverse *resolution* from the inverted per-action `resolution_map`. (§4.1) |
| Sync stop (v4) | **A Jira label** (`sync_stop_label`, per action, default unset) halts the pair in **both** directions; removing it resumes from the current state, with no replay of what was missed. (Invariant E) |
| Loop prevention | **Symmetric by design.** Both inbound paths drop events authored by JBI's own account — Jira `accountId` inbound, `WebhookEvent.user.login` on the Bugzilla side — backed by read-before-write idempotency. (Invariant C) |
| State store | **No new datastore in Phase 1.** Correlation reuses the existing `see_also`/remote-link; identity lives in YAML; loop-prevention is stateless. Redis is considered only if multi-instance ephemeral state later proves necessary. |
| Conflict policy | **BMO wins for execution fields; Jira wins for planning-only fields.** (Defined in §4.) |
| Pilot | **Core :: Machine Learning: On Device.** |
| Identity mapping | **Email-first automatic resolution + a machine-seeded YAML override file for mismatches only + reconciliation-driven upkeep.** (§5.) |
| Change discipline | **Extend, don't break.** All new behavior is additive, config-gated, and default-OFF; only the pilot opts in. (§6.) |
| Build vs. integrate | **Integration path** (extend JBI) rather than extending BMO. Recorded in ADR `docs/adrs/004-bidirectional-sync.md`. |

**Rationale**

- **Push over polling** because JBI is already a webhook receiver: a second
  inbound endpoint reuses the existing auth, dead-letter queue, and executor
  pattern rather than introducing a new scheduled-poller subsystem. Push is
  real-time (comfortably inside the PRD's 5-minute SLA) and its cost scales with
  the number of *actual* changes, not with a polling interval. Jira Automation is
  already part of JBI onboarding today, so the mechanism is not new operationally.
  The rule is **central and multi-project, not per-project**: prod config already
  covers 35 Jira projects, so a per-project rule would mean 35 hand-built,
  independently-drifting copies owned by 35 different project admins. One rule
  scoped by JQL keeps onboarding, auditing, and revocation in one place (§12).
- **Pub/Sub over a direct web request** because the broker already solves
  what we would otherwise hand-roll: retry with backoff, dead-lettering, and a
  buffer when JBI is down.
- **Pull rather than push** (v5, corrected from v4) because pull gives
  explicit control over concurrency and ordering: flow control decides how
  many messages are in flight, and ordering keys keep a single bug's events
  in sequence — which the reverse conflict rule depends on, since it compares
  against "the value before this change". The costs are real and accepted: a
  second deployment unit, a bounded pull window, and signal handling. Two
  operational lessons are taken from the reference implementation rather than
  relearned — flow control must never be `max_messages=1` (it serialises every
  ordering key behind one slot and strands held messages at teardown), and
  messages still held when the window closes are abandoned and redelivered,
  which the log must say out loud.
- **Symmetric loop prevention** because a write is a write in both systems: a
  reverse write into BMO fires BMO's normal webhook and re-enters the forward
  pipeline exactly like a human edit would. Suppressing echoes on only the Jira
  side leaves that half of the loop open, so the actor check is applied on both
  inbound paths (Invariant C).
- **No new datastore** because every piece of state we need already has a home:
  the bug↔issue correlation is the `see_also` link (BMO) plus the remote link
  (Jira); the identity overrides are near-static and belong in version-controlled
  config; and loop-prevention can be made stateless (see §5, §9-D6/D7). Adding a
  database or Redis now would be cost and operational burden with no payback yet.
- **BMO-wins-for-execution** reflects the PRD's separation of concerns: BMO is
  the system of record for *what the work is and its technical state*; Jira is the
  system of record for *how that work maps to delivery*. The conflict rule simply
  encodes who owns what (§4).
- **Extend, don't break** because JBI is in active production use for many teams;
  a regression in the existing Bugzilla→Jira path would affect projects far beyond
  this pilot. Every deliverable is therefore reversible by flipping a flag.

---

## 2. Problem statement

The PRD is motivated by four recurring, concrete problems: (1) engineering work
is deliberately split across BMO and Jira; (2) Jira is the planning tool, which
forces manual, error-prone duplication of BMO work; (3) leadership reports out of
Jira, so BMO-only work is invisible to planning; and (4) keeping the two systems
aligned by hand causes tickets to be misplaced, wrongly closed, or drift out of
sync.

JBI already automates part of this, but **only in one direction.** A single
inbound endpoint (`jbi/router.py` `/bugzilla_webhook`) feeds an `Executor`
(`jbi/runner.py`) that runs a configurable list of step functions
(`jbi/steps.py`), all of which write to Jira. The only value ever written *back*
to BMO is the `see_also` link that records the Jira issue URL
(`jbi/bugzilla/service.py add_link_to_see_also`). Concretely, that means a status
change, comment, or reassignment made in Jira never reaches BMO — the manual
reconciliation the PRD wants to eliminate still has to happen by hand in the
Jira→BMO direction.

The PRD asks for three capability areas on top of today's behavior:
**bidirectional field sync**, **metabug→epic hierarchy modeling**, and
**release-data / identity / visibility** handling. Of these, the reverse
(Jira→BMO) direction is the architectural keystone: most of Phase 1's new value
depends on JBI being able to receive and act on Jira-side changes at all, which
it currently cannot.

---

## 3. Invariants (hold across all phases)

These are correctness rules the whole system must never violate. They are listed
first because several later design choices exist specifically to preserve them,
and every PR is expected to keep the tests that enforce them green.

**Invariant A — never create a duplicate Jira issue.**
When a BMO bug already links to a Jira issue (recorded in `see_also`), sync must
*update* that issue, never create a second one. This matters concretely at
rollout: the pilot component already contains many bugs that were linked to Jira
under the current system, and a naive "create on sync" would flood Jira with
duplicates of work that already exists. *This is already enforced today* —
`runner.py do_execute_actions` reads the linked key from `see_also` and routes
linked bugs to UPDATE, and even the "whiteboard tag added to an already-linked
bug" edge case flows through `steps.py create_issue`, which detects the existing
issue and updates it instead of creating. The plan preserves this behavior and
adds a permanent regression test for it (D12).

**Invariant B — Jira→BMO is write-back only; it never creates a BMO bug.**
BMO is authoritative for *what work exists* (PRD §2), so a Jira issue must never
cause a new Bugzilla bug to be filed. The reverse executor enforces this by
resolving the linked BMO bug from the inbound Jira issue (via the Bugzilla remote
link, falling back to `see_also`); **if no linked bug exists, the event is
ignored outright.** A standalone Jira issue — for example, cloud/service work
that never had a bug — therefore has no reverse effect at all. This is the mirror
image of Invariant A: A prevents duplicate Jira issues, B prevents spurious BMO
bugs.

**Invariant C — a sync must never echo back into the system it came from.**
Every write JBI makes is, to the receiving system, an ordinary change: it fires
that system's normal webhook and re-enters the *other* direction's pipeline.
Loop-safety therefore has to be symmetric, and v2 only had half of it.

- *Jira → BMO (already in v2):* an inbound Jira event authored by the JBI service
  account is dropped, so JBI's own Jira writes don't come back at it.
- *BMO → Jira (added in v3):* the same check now runs on the forward path. When
  a reverse write lands in Bugzilla, BMO fires `/bugzilla_webhook` as usual; the
  forward pipeline compares the event's actor — `WebhookEvent.user.login`, which
  the payload already carries (`jbi/bugzilla/models.py`) — against JBI's
  configured Bugzilla account and drops the event before any Jira write.
- *Backstop:* the actor check is an optimization as much as a guard, and it is
  not sufficient on its own — `WebhookEvent.user` is `Optional`, and an
  admin-run or migration-driven change can arrive with no actor. The second line
  of defence is D7's read-before-write: a write whose value already matches is a
  no-op, so even an unsuppressed echo terminates after one round trip instead of
  oscillating.

**Verified live (2026-09-24).** A Jira Automation rule and a BMO webhook were
pointed at a tunnelled JBI. With `bugzilla_bot_login` unset, one Jira
transition produced one BMO write, which came back through the webhook and
drove a forward pass that posted **two comments** onto the issue — the field
writes were idempotent, but comment posting has no read-before-write
equivalent, so the echo showed up as comment noise rather than as changed
data. With the login set, three transitions produced three BMO writes, all
three resulting webhooks arrived and all three were dropped, and no forward
pass ran at all.

**Comments need a third layer (added after live testing).** Field echo is
bounded — read-before-write makes the second write a no-op. Comment echo is
not: each hop rewraps the text in another attribution layer, so the body
differs every time, no duplicate check matches, and the comment grows without
bound. Observed live when an identity gate was unset. Comments therefore also
carry **content markers** (`jbi/sync_markers.py`): each direction recognises
the other's output and refuses to re-import it, independently of
configuration. Three variants had to be recognised before this held, each
found by running it:

- the marker as JBI writes it (`*user@example.com* commented:`);
- the same marker after **Jira re-renders it** (`_[mailto:user@example.com]_
  commented:`) — a marker is not read back in the form it was written;
- the forward path's **change comments**, which are a JSON blob
  (`{"modified by": …, "status": …}`) carrying no prose marker at all.

Why both layers are needed: without the actor check, every reverse write costs a
wasted BMO→Jira round trip. Without read-before-write, a value that does *not*
survive the round trip identically — which is exactly the risk the non-invertible
`status_map` creates (§4.1) — would be rewritten with a *different* value each
pass, corrupting the field rather than merely wasting a call.

**Invariant D — confidential content never crosses, in either direction.**
Both systems hold restricted work, and both can publish it to an audience the
other never intended. The rule is symmetric and fails *closed*.

- *BMO → Jira:* a bug is restricted when `is_private` is true **or** `groups`
  is non-empty. `is_private` alone is insufficient — it is an optional payload
  field, so an absent value reads as "public", while `groups` is the actual
  mechanism behind security, embargoed and employee-only bugs. Restricted bugs
  are never synced at all, checked on the inbound payload *and again on the
  refreshed bug*, because a bug can gain a group while the event sits in the
  dead-letter queue. Independently, private **comments** and private
  **attachments** on otherwise-public bugs are never copied into Jira.
- *Jira → BMO:* free text (comment bodies and summaries) is not copied when
  the comment carries a `visibility` restriction, when it is JSM
  internal-only, or when the issue has an **issue security level** set.
  Restricted bugs receive no reverse writes of any kind.
- *Why fail closed:* the Automation payload is assembled by a rule JBI does
  not control, so "the field is absent" cannot be read as "there is no
  restriction". Copying Jira text to BMO therefore requires an explicit
  per-action opt-in (`reverse_comment_sync_enabled`, default off), which is an
  operator asserting the rule sends those fields.
- *Scope:* the guard covers free text, not enumerated values. A status or
  priority carries no embargoed detail, and blocking it would silently strand
  a bug's state.

**Invariant E — a human's stop label outranks everything.**
A configured label on the Jira issue (`sync_stop_label`) halts syncing for
that bug/issue pair in **both** directions, and removing it resumes. It is
checked before any write in either direction, so it overrides scope,
thresholds, field ownership and conflict policy alike.

- *Both directions*, because that is what a user means by "stop syncing this";
  a label that silently stopped only one direction would be a trap.
- *No replay on resume.* Syncing picks up from the current state; changes made
  while stopped are not reapplied, because nothing records them. A user who
  needs the intervening history has it in both systems' own change logs.
- *Creation is unaffected*: an unlinked bug has no Jira issue to carry a
  label, so the escape hatch exists only once a pair exists.
- *Cost*: none on the forward path. The linked Jira issue is already fetched
  for the project check, so its labels come back in that same call.

**Consequence — the two directions are deliberately asymmetric.**
BMO→Jira may CREATE-or-UPDATE; Jira→BMO is UPDATE-only. Beyond enforcing
Invariant B, the same correlation gate that finds "the bug behind this issue"
also scopes loop-prevention: JBI only ever writes back to bugs whose link it
already owns, which bounds the set of writes it can possibly emit.

---

## 4. Field ownership and conflict policy

Because sync is becoming bidirectional, the same field can now be edited on both
sides. To keep that deterministic, every field has a single authoritative system,
derived from the PRD's separation of concerns. The guiding principle: **BMO is
the system of record for the technical reality of the work; Jira is the system of
record for how that work is planned and delivered.**

**Execution fields — BMO authoritative.** These describe *what the work is and
its technical state*. On a same-window conflict (both sides edited before sync
reconciles), the BMO value wins and Jira is overwritten, because BMO is where the
engineering truth lives.
- Synced in both directions, BMO-wins: **Summary, Status/Resolution, Assignee,
  Priority, Severity.**
- BMO-owned structural data, **mirrored to Jira but never written back from
  Jira:** Product & Component, Blocks/Depends-On (metabug structure), Release
  flags, Target Milestone, Flags & Keywords. These have no meaning to overwrite
  from Jira — changing them is a BMO-side engineering act.

**Planning fields — Jira authoritative, never written back to BMO.** These
describe *how work maps to delivery* and mostly have no BMO equivalent. Writing
them back to BMO would either fail (no such field) or pollute a public bug with
internal planning context. This is the Phase-1 "write-back suppression."
- **Epic membership/parent, Sprint, Milestones, Story Points / RICE** (where BMO
  has no such field), roadmap / project org.

**Implementation.** A single `WRITEBACK_DENYLIST` (the planning fields) is
consulted by every Jira→BMO step, so suppression is enforced in one place rather
than scattered across writers. A per-field "authoritative source" table is used
only to resolve the rare same-window conflict. Keeping both concerns centralized
means a reviewer can audit the policy by reading one module.

> Note (PRD §7): Story Points is *not* universally Jira-only — some BMO
> components have it enabled. The denylist is therefore evaluated per-component in
> Phase 2; for the Phase-1 pilot we treat Sprint / Story Points / Epic as
> Jira-only and suppress their write-back.

### 4.1 Reverse status & resolution mapping (why `status_map` is not inverted)

**The problem.** v2 said the Jira→BMO close path would "reuse the existing
status/resolution maps." Review correctly rejected that: **`status_map` is
many-to-one and cannot be inverted.** In prod config today, 30 of 43 actions
define a `status_map`, and the collapsing is severe — e.g. the `fxcm` action maps
`RESOLVED, VERIFIED, FIXED, INVALID, WONTFIX, INACTIVE, DUPLICATE, WORKSFORME,
INCOMPLETE, MOVED` all to the single Jira status `Done`. Given `Done`, there is
no way to recover which BMO status/resolution produced it. Worse, the collapsing
differs per project's workflow (`fidefe` uses `Closed`, `fxdroid` uses `Done` and
`In Eng`), so a literal reverse map would be **N hand-maintained maps** — the
same per-project sprawl rejected for Automation rules in §12. It is also the
concrete mechanism behind the corruption risk in Invariant C: a status that does
not round-trip identically gets *rewritten wrong*, not merely rewritten.

**The design.** Split the problem, because the two halves have different shapes.

*Status — derive from `statusCategory`, not from status names.* Every Jira
status, in every project's custom workflow, belongs to exactly one of three
built-in categories, and the inbound payload carries it as
`fields.status.statusCategory.key`. That gives **one project-agnostic default
map** instead of N:

| Jira `statusCategory.key` (colour) | BMO status written |
|---|---|
| `new` (blue-gray) | **nothing** — except `REOPENED` when the bug is currently resolved (see below) |
| `indeterminate` (yellow) | `ASSIGNED` |
| `done` (green) | resolved — see the resolution rule below |

The `new`-category rule is deliberately asymmetric: **reverse status only ever
moves a bug forward.** Verified against the pilot project's real workflow, that
category contains Backlog, To Do *and* **Blocked** — so mapping it to `NEW`
would regress an in-progress bug to "never worked on" whenever someone marked
the Jira issue blocked. Because overrides are keyed by category, `Blocked` cannot
be distinguished from `To Do`; and BMO does not model blocking as a status at all
(it uses `depends_on`), so there is nothing to mirror. The one transition in that
category worth writing is a genuine reopen, where BMO's `REOPENED` preserves the
fact that the bug was once closed. The current BMO status comes from D7's
read-before-write fetch, so this costs no extra call. A project whose
`new` statuses really do mean "not started" can opt back in with an explicit
`reverse_status_overrides` entry.

*Resolution — invert `resolution_map`, which unlike `status_map` actually is
invertible.* `resolution_map` maps a BMO resolution to a Jira **resolution**
field value (`FIXED → Done`, `WONTFIX → Won't Do`, `DUPLICATE → Duplicate`, …).
We checked every prod config that defines one: **all 17 are injective** — no two
BMO resolutions collapse onto the same Jira resolution — so inverting them is
mechanically safe and needs no new hand-written config. The inverse is computed
at config-load time, and **config validation fails loudly if a future
`resolution_map` is non-injective**, so this property is enforced rather than
assumed.

*The ambiguous residue — named, not guessed.* Green/`done` says a bug is finished
but not *why*, and the Jira resolution field may be unset. Precedence:

1. Jira resolution field set and present in the inverted `resolution_map` → write
   that BMO resolution.
2. Unset or unmapped → write the action's new optional
   `default_reverse_resolution` (proposed default `FIXED`, since a human closing
   an issue in a delivery project overwhelmingly means fixed).
3. No default configured → **write the status transition but leave the resolution
   untouched**, log at WARN, and surface the bug in the R-13 reconciliation report
   for a human. Never guess a resolution: a wrong `DUPLICATE` or `WONTFIX` is a
   factual claim about the bug that misleads everyone reading it later.

**Config surface** (all optional, default-OFF, per constraint §6-3): a global
`REVERSE_STATUS_CATEGORY_MAP` default as tabled above, an optional per-action
`reverse_status_overrides` for projects whose workflow genuinely needs different
BMO targets, and `default_reverse_resolution`. The pilot ships on the defaults;
no other project is touched.

**What this costs.** Reverse status sync is deliberately lower-fidelity than
forward: Jira's three categories cannot express BMO's full status vocabulary, so
a Jira move to `In Eng` and to `In Review` both write `ASSIGNED`. That is the
correct trade — BMO is authoritative for execution state (§4), so the reverse
direction only needs to keep BMO from being *stale*, not to mirror Jira's
workflow granularity into it.

---

## 5. Identity mapping (R-11)

**The problem this solves.** Assignee sync and comment attribution both require
naming the *same human* in both systems. But the two systems identify people
differently: **BMO identifies a user by email** (the account effectively *is* an
email, e.g. `assigned_to: "person@mozilla.com"`), while **Jira Cloud identifies a
user by an opaque `accountId`** whose associated email may differ from the BMO
one or be hidden for privacy. Today's code assumes the two emails match — it looks
up the Jira user *by the BMO email* (`jira/service.py find_jira_user`) and clears
the assignee when that fails. R-11 exists precisely because that assumption breaks
for real people (the PRD cites a reviewer whose BMO ID and work email differ).

**Design goal:** correctness *without* a standing maintenance burden. We achieve
that by making the map hold only the cases automatic resolution cannot handle,
rather than a roster of everyone.

- **Resolution order at runtime (a three-tier cascade).** For any person we need
  to resolve, JBI tries, in order: **(1)** the YAML override map; **(2)**
  automatic email lookup against the target system (`find_jira_user`); **(3)** a
  safe fallback — leave the assignee unset/cleared and attribute comments in text.
  The order is deliberate: an explicit override is the most trustworthy, automatic
  email match handles the common case, and the fallback never *guesses* an
  identity or *drops* a comment. Guessing risks assigning work to the wrong person
  and dropping a comment loses information — both are worse failures than an
  unset assignee that a human can correct.
- **The map holds exceptions only.** Most people have the same corporate email in
  both systems, so tier (2) resolves them with no map entry — including new hires,
  who get standard corporate email in both systems and therefore "just work" the
  first time they're assigned. The map is only for genuine mismatches or
  hidden-email users. This is what keeps it small and stops it from becoming a
  directory we have to hand-maintain.
- **Storage.** A new global file `config/identity_map.{env}.yaml`, kept separate
  from the `Actions` config so the existing config parser (which expects a flat
  list of actions) is untouched — an "extend, don't break" requirement (§6). The
  Jira side of each entry is keyed on **`accountId`, not email**, because
  `accountId` is stable whereas a Jira email can change or be hidden.
- **Seeding, so the file is never hand-typed.** `bin/seed_identity_map.py`
  bulk-pulls the Jira user directory for the configured projects and resolves
  `email → accountId` in one pass. This bootstraps the file and detects drift
  (e.g. an accountId that no longer exists), rather than relying on someone
  editing YAML by hand and getting an opaque ID right.
- **Upkeep is alert-driven, not manual polling.** When runtime resolution fails
  for an *active* user, JBI logs it and the case is surfaced in the R-13
  reconciliation report. So the system tells us "these people need an override
  entry this week" instead of anyone proactively curating the list.
- **No impersonation.** Comment write-back always posts as the JBI service
  account, prefixed "from Jira, by \<name\>"; the identity map supplies only the
  *display name*. We deliberately do not post *as* the person: JBI holds one set
  of credentials, impersonation would be a trust/permissions problem, and the
  explicit "from Jira" marker is also what makes the round trip distinguishable
  for loop-prevention (§9-D6) and satisfies PRD Acceptance Scenario 4's
  attribution requirement.
- **Unassigned sentinel.** BMO uses `nobody@mozilla.org` to mean "unassigned"
  (already special-cased by `bug.is_assigned()`); the resolver treats it as
  "no one," never as a person to look up.
- **Assumption to verify.** The email-first path depends on the JBI-target Jira
  instance exposing user email to the service account (Jira Cloud can hide it).
  This is confirmed for `mozilla-hub`; we must confirm the deployed instance is
  the same or is configured to expose email to JBI's account. If a target instance
  hides email, we lean harder on the seed script and, longer term, a
  directory/IdP-backed resolver — explicitly out of scope for the pilot.

---

## 6. Cross-cutting constraints ("extend, don't break")

JBI is in active production for many teams, so the overriding constraint is that
this work must not regress the existing Bugzilla→Jira path. These rules make that
concrete and, equally important, make each PR safe and easy to review.

1. **New sync behaviors are additive step functions, config-gated, default-OFF.**
   The `Executor` already composes behavior from a configured list of steps, so
   new capability is added as new steps that simply don't run unless a config
   opts in. Nothing changes for existing tags.
2. **The inbound Jira path is a separate endpoint.** `/jira_webhook` is new and
   isolated; `/bugzilla_webhook` and its code path are untouched, so the reverse
   direction cannot destabilize the forward one.
3. **All new model fields are optional with safe defaults.** Every existing
   `config/config.*.yaml` must continue to parse unchanged; adding a field must
   never force a config migration.
4. **The suite stays green and the pilot is the only opt-in.** New behavior ships
   behind config and is enabled only for Core :: Machine Learning: On Device, so
   production impact is contained and a problem is contained with it.
5. **Reuse over rebuild.** Where the PRD overlaps existing behavior, we extend it:
   R-02/R-03 build on `maybe_add_phabricator_link`; Jira→BMO close reuses the
   existing `resolution_map` **by inversion** (safe: injective in every prod
   config, and validated at load) while deriving status from Jira's
   `statusCategory` rather than inverting the many-to-one `status_map` — see
   §4.1, which supersedes v2's blanket "reuse the existing status/resolution
   maps"; the reconciliation job reuses the standalone
   scheduled-runner pattern already established by `jbi/retry.py`. This keeps the
   surface area — and the review burden — small.

**Why this shape helps review:** because every deliverable is additive and
flag-off, each PR can be merged without changing production behavior, and rollback
is a config flip rather than a revert. Reviewers can reason about one isolated,
inert-by-default change at a time.

---

## 7. Architecture

### Current (as-is)

Today JBI is a linear, one-way pipeline. Bugzilla posts an event; JBI enqueues it
for resilience, looks up which configured action(s) apply, and runs their steps
against Jira. The only BMO write is the `see_also` back-link.

```
Bugzilla --POST /bugzilla_webhook--> execute_or_queue --> Executor --> steps.py --> Jira
                                          |                                |
                                   DeadLetterQueue                   (many writes)
                                                                          |
                                                                   BMO write: see_also link only
```

### Target

We add a **second, symmetric inbound path for Jira**, and a set of reverse steps
that write to BMO through an extended `bugzilla_service`. The forward path is
unchanged **except for one additive gate**: it now drops events authored by JBI's
own Bugzilla account, which closes the other half of the loop (Invariant C). The
reverse path is guarded at three points before any write: it
correlates the issue back to a bug (Invariant B), suppresses events authored by
JBI's own service account (loop-prevention), resolves identities and enforces
visibility, and applies the write-back denylist (§4).

```
                    v5 transport: both sources publish to one topic
Bugzilla --event--> [ Pub/Sub topic ] <--event-- Jira Automation (central, JQL-scoped)
                            |
                 pull subscription (ordering key = bug id)
                            |
                 `python -m jbi consume`  -- its own process
                   streaming pull, flow control, SIGTERM-aware
                            |
                     jbi.ingest.ingest_event(InboundEvent) -> IngestResult
                            |            (dedupe on delivery_id)
                     ack / nack from the outcome
              +-------------+-------------+
              |                           |
        source=bugzilla             source=jira
              |                           |
              v                           v
        (forward pipeline)          (reverse pipeline)

The two pipelines themselves are unchanged from v3, and the direct HTTP
endpoints remain for compatibility and local testing:

Bugzilla --POST /bugzilla_webhook--> [echo gate] --> execute_or_queue --> Executor --> steps.py --> Jira
   ^                                     ^                |                                  |
   |                        (NEW: drop if event.user.login  DeadLetterQueue           (otherwise unchanged)
   |                         == JBI's BMO account)
   |
   +-- bugzilla_service writes <- jira_steps.py <- ReverseExecutor <- execute_or_queue <- POST /jira_webhook <- ONE central
       (status/assignee/priority/          |                     ^                                             multi-project
        summary/comment, guarded)  identity + visibility   (echo-suppressed:                                   Automation rule
                                   + writeback denylist     ignore JBI-bot actor;                              (JQL-scoped)
                                   + statusCategory map     ignore uncorrelated issue)
                                     (§4.1)
```

The two `[echo gate]`s are the same rule applied at both entry points, which is
what makes Invariant C hold: neither system's copy of a value can re-enter the
pipeline that produced it.

**New modules (v4)**
- `jbi/ingest.py` — the one seam every transport calls, plus duplicate
  suppression keyed on the broker message id. The suppression cache is
  bounded and **in-process**: adequate for the redelivery bursts a broker
  produces, explicitly not a distributed guarantee. A shared store is the
  real answer and is deferred rather than half-built (§13-12).
- `jbi/consumer.py` — the streaming-pull consumer: message decoding, source
  detection (`event_source` attribute first, payload shape as fallback), the
  permanent-vs-transient distinction, ack/nack, flow control and shutdown.

**New modules (v3)** — kept as new files so the diff is legible and the forward path is
untouched:
- `jbi/jira_inbound/` — the Jira event model and the `/jira_webhook` endpoint.
- `jbi/jira_steps.py` — the reverse step functions (the Jira→BMO equivalents of
  `jbi/steps.py`), driven by a `ReverseExecutor`.
- `jbi/identity.py` — the identity resolver (§5).
- `jbi/visibility.py` — the public/private write-back guard (R-12).
- `jbi/hierarchy.py` — metabug→epic modeling (Phase 2).
- `jbi/reconcile.py` — the reconciliation report job (Phase 3).
- `bin/seed_identity_map.py` — identity-map seeding/drift detection.

**Extended (in place, additively):**
- `jbi/router.py` / `jbi/runner.py` — the forward-path echo gate (Invariant C),
  a single early-return on actor match.
- `jbi/environment.py` — `Settings` gains `bugzilla_bot_login` and
  `jira_bot_account_id`: the two identities the echo gates compare against.
  These live in `Settings`, not per-action config, because there is exactly one
  JBI service account per deployment.
- `jbi/bugzilla/service.py` — new write methods layered on the existing generic
  `client.update_bug`.
- `jbi/bugzilla/models.py` — typed release-flag and Target-Milestone fields.
- `jbi/models.py` — new optional `ActionParams` toggles (scope, threshold,
  identity, inbound).

---

## 8. Requirement → change map

This is the traceability spine: every PRD requirement, its current status in the
codebase, and the specific files/functions a PR will touch. Verdicts:
✅ Satisfied · 🟡 Partial (foundation exists) · ❌ Missing.

| Req | Verdict | Files / functions to add or extend |
|---|---|---|
| **R-01** scope by Product/Component | 🟡 extend | `models.py ActionParams` add `sync_products_components`; gate in `runner.py lookup_actions` / `do_execute_actions`; verify BMO webhook registration for pilot (`bugzilla/service.py check_bugzilla_webhooks`). |
| **R-02** patch opened → In Review | 🟡 extend | `steps.py maybe_add_phabricator_link` + `jira/service.py update_issue_status` + `jira/client.py get_issue_transitions_with_fields`. Skip if issue is in a terminal state. |
| **R-03** "requires changes" → out of In Review | ❌ (signal exists) | Read `AttachmentFlag{name,value}` (already modeled); new step `sync_phabricator_review_state`. |
| **R-04** priority/severity threshold | ❌ | `ActionParams` `min_priority`/`min_severity`; early-ignore gate in `runner.py`. |
| **Field sync BMO→Jira** | ✅ done | Existing steps: summary/status/assignee/priority/comment. No change. |
| **Field sync Jira→BMO** (PRD §6.2 table) | ❌ | `jira_steps.py` writers + `bugzilla/service.py` new methods over `client.update_bug`. |
| **Idempotency / loop prevention** | ❌ | **Symmetric** service-account echo-suppression — inbound Jira (`accountId`) *and* forward Bugzilla (`WebhookEvent.user.login`, `runner.py`/`router.py`) — plus read-before-write idempotency as backstop. Stateless (no store). Invariant C, D6/D6b/D7. |
| **R-05** metabug→epic | ❌ | `jbi/hierarchy.py` + `jira/service.py create_epic/find_epic`; step `ensure_metabug_epic`. |
| **R-06** bug-under-metabug→epic task | ❌ | `hierarchy.py`: on CREATE, parent under the metabug's epic where applicable. |
| **R-07** re-parent never writes to BMO | 🟡 enforce | Add epic/parent to `WRITEBACK_DENYLIST`; reverse steps ignore parent-change events. |
| **R-08** BMO link preserved after re-parent | ✅ / guard | Existing `see_also` + remote link (`extract_from_see_also`, `add_link_to_bugzilla`); assert untouched by re-parent. |
| **R-09** release flags → Jira | ❌ | Type `cf_status_firefoxNN` on `Bug`; step `mirror_release_flags`. |
| **R-10** Target Milestone → Jira | ❌ | Add `target_milestone` to `Bug`; step `mirror_target_milestone`. |
| **R-11** identity matching | ❌ | `jbi/identity.py` + `config/identity_map.{env}.yaml` + `bin/seed_identity_map.py` (§5). |
| **R-12** visibility on write-back | ❌ | `jbi/visibility.py`: `bug_restriction_reason` (BMO `groups`/`is_private`, used by *both* directions) + `can_copy_jira_text_to_bug` (Jira `comment.visibility`, `jsdPublic`, `fields.security`). Forward path also drops private comments/attachments. Gated by `reverse_comment_sync_enabled`, default off. See Invariant D. |
| **R-13** reconciliation report | ❌ | `jbi/reconcile.py` standalone job (retry.py pattern). |
| **T-01** transport-agnostic ingestion (v4) | ✅ done | `jbi/ingest.py`: `InboundEvent` envelope, `IngestResult` ack decision, message-id dedupe. Every transport converges here. |
| **T-02** Pub/Sub pull consumer (v5) | ✅ done | `jbi/consumer.py` + `python -m jbi consume`. Streaming pull, flow control (never 1), ordering keys, `await_callbacks_on_shutdown`, SIGTERM handling, bounded pull window. IGNORED/PERMANENT ack; RETRY and unexpected errors nack. |
| **T-03** sync-stop label (v4) | ✅ done | `writeback.sync_is_stopped`, gated in `runner.do_execute_actions` (forward) and `jira_inbound.handler` (reverse). Invariant E. |

---

## 9. Phase 1 — testable deliverables (PR-sized)

**Sequencing philosophy.** The forward-direction items (D2–D5) are independent
and low-risk, so they can land in any order after the scaffolding. The
reverse-direction items are strictly ordered so that **no reverse write can ever
land before the safety machinery that governs it**: the inbound spine and its
correlation/echo gate (D6), the forward-path echo gate (D6b), and idempotent
writes (D7) all merge *before* any field writer (D9/D10) is enabled. Each deliverable is additive, config-gated, and
default-OFF, ships with its own tests, and keeps the full suite green.

Format per deliverable: *what it accomplishes · requirement · files · tests ·
acceptance · depends-on · review note.*

### Forward-direction (independent, low-risk)

**D1 — Scaffolding & config model.**
*Accomplishes:* gives every later deliverable a config flag to hang behavior on,
with zero behavior change today. · *Req:* enables constraint #4. · *Files:*
`jbi/models.py` (`ActionParams`: optional `sync_products_components`,
`min_priority`, `min_severity`, `identity_map_enabled`, `jira_inbound_enabled`,
all default to no-op); ADR `docs/adrs/004-bidirectional-sync.md`. · *Tests:*
`tests/unit/test_models.py`, `test_configuration.py` — existing configs still
parse; defaults reproduce current behavior. · *Acceptance:* zero behavior change
with default config. · *Depends:* — · *Review note:* pure additive schema; small,
obviously safe.

**D2 — Product/Component scope gate (R-01).**
*Accomplishes:* lets a component be opted into sync without flooding Jira with
pre-triage noise. · *Files:* `runner.py do_execute_actions` / `lookup_actions`. ·
*Tests:* `test_runner.py` with `WebhookRequestFactory`/`BugFactory` — in-scope bug
syncs; out-of-scope bug produces no Jira call. · *Acceptance:* **PRD Scenario 2.**
· *Depends:* D1.

**D3 — Priority/severity threshold (R-04).**
*Accomplishes:* restricts sync to actionable bugs (e.g. P1/P2, S1/S2) so
low-signal bugs don't reach Jira. · *Files:* `runner.py` gate. · *Tests:*
`test_runner.py` — sub-threshold → no create; at/above → create. · *Acceptance:*
below-threshold bug creates nothing. · *Depends:* D1.

**D4 — Phabricator patch-opened → In Review (R-02).**
*Accomplishes:* moves the Jira issue to In Review when a patch is posted, so Jira
reflects real review state. · *Files:* `steps.py maybe_add_phabricator_link`;
`jira/service.py update_issue_status` + `client.get_issue_transitions_with_fields`.
· *Tests:* `test_steps.py` with `context_attachment_example` +
`WebhookAttachmentFactory` — patch-open transitions to In Review; a terminal issue
is left alone. · *Acceptance:* patch opened → Jira In Review. · *Depends:* D1.

**D5 — Phabricator "requires changes" → out of In Review (R-03).**
*Accomplishes:* moves the issue back out of In Review when a reviewer requests
changes, so Jira doesn't imply reviewers are the bottleneck when the author owns
the rework. · *Files:* new `steps.py sync_phabricator_review_state` reading
`AttachmentFlag`. · *Tests:* `test_steps.py` — `review-` transitions out;
`review+`/`review?` do not. · *Acceptance:* requires-changes moves out of In
Review. · *Depends:* D4.

### Reverse-direction (strictly ordered; loop-safety is intrinsic)

**D6 — Jira inbound spine (infrastructure).**
*Accomplishes:* the entire ability to receive and act on Jira-side changes — the
keystone every reverse requirement sits on. *Traces to:* PRD Phase-1
"Bidirectional field sync"; it is the enabling infrastructure for the PRD §6.2
Jira→BMO table and Acceptance Scenarios 3 & 4 (it is not itself an R-number). ·
*Files:* `jbi/jira_inbound/models.py` (Jira event model), `router.py` (new
`POST /jira_webhook`, reusing `api_key_auth`), `jbi/jira_steps.py`
(`ReverseExecutor` skeleton), reusing `DeadLetterQueue`. The handler correlates
issue→bug and **ignores the event** if either (a) no linked bug exists
[Invariant B] or (b) it was authored by the JBI service account [loop-prevention].
It performs **no writes yet.** · *Tests:* `tests/unit/jira_inbound/` +
`test_router.py` — auth parity (401 without key); a valid event enqueues/acks; an
uncorrelated issue is a no-op; a bot-authored event is a no-op. · *Acceptance:*
**Invariant B** (uncorrelated inbound → no BMO write, no bug creation). · *Depends:*
D1. · *Review note:* no behavioral risk — the endpoint only logs/correlates and
mirrors the proven `/bugzilla_webhook` shape.

**D6b — Forward-path echo gate (Invariant C, second half).**
*Accomplishes:* stops a reverse write into BMO from bouncing straight back into
Jira through the existing forward pipeline — the gap review identified in v2. ·
*Traces to:* Invariant C; enabling safety for D9/D10 (not an R-number). ·
*Files:* `jbi/environment.py` (`Settings.bugzilla_bot_login`), and a single early
IGNORE in the forward path keyed on `WebhookEvent.user.login` — placed in
`runner.py` alongside the existing ignore/short-circuit logic so it is logged and
observable the same way as every other skipped event, rather than silently
dropped at the router. Handles `event.user is None` by **proceeding** (fail-open),
because a missing actor is an ordinary BMO payload shape, not evidence of an
echo; D7's read-before-write covers that case instead. · *Tests:*
`test_runner.py` with `WebhookRequestFactory` — an event authored by the bot
login produces zero Jira calls and an IGNORE log; the same event authored by a
human syncs normally; a `user is None` event syncs normally. · *Acceptance:*
**Invariant C** in the BMO→Jira direction: a JBI-authored BMO change causes no
Jira write. · *Depends:* D1. · *Review note:* one early-return plus one setting;
inert until a bot login is configured (unset default = current behavior exactly).

**D7 — BMO write service + idempotent writes.**
*Accomplishes:* the low-level ability to write execution fields back to BMO,
built so that an echoed value is a silent no-op — the backstop layer of
Invariant C, which holds even when the D6/D6b actor checks cannot fire (missing
actor, admin-run change). · *Files:* `bugzilla/service.py` new methods
(`set_status_resolution`, `set_assignee`, `set_priority`, `set_summary`,
`add_comment`) over the existing `client.update_bug`; read-before-write (reusing
`refresh_bug_data`) so writing a value that already matches issues no update. ·
*Tests:* `test_service.py` with `responses`/mocked client — each writer maps
correctly; a write is skipped when the value already matches (the loop-safety
unit proof). · *Acceptance:* a round-trip write of an unchanged value issues no
BMO update. · *Depends:* — (service layer, unwired). · *Review note:* isolated
and independently unit-tested.

**D8 — Identity map (R-11).**
*Accomplishes:* correct person resolution for assignee and attribution without a
maintenance burden (§5). · *Files:* `jbi/identity.py`,
`config/identity_map.{env}.yaml`, `bin/seed_identity_map.py`; wired into the
forward `maybe_assign_jira_user` first, as the lowest-risk first use. · *Tests:*
`test_identity.py` — override hit; email fallback; unresolved fallback
(leave/clear); `nobody@mozilla.org` sentinel; seed script against a mocked Jira
user directory. · *Acceptance:* a mismatched-email user resolves via the map; an
unmapped user resolves by email; an unresolved user degrades gracefully. ·
*Depends:* D1.

**D9 — Reverse field writers (PRD §6.2 Jira→BMO).**
*Accomplishes:* the actual bidirectional field sync for execution fields. ·
*Files:* `jira_steps.py` — `writeback_status`/`_resolution`, `_priority`,
`_assignee` (using D8), `_summary`; behind `jira_inbound_enabled`; enforcing the
`WRITEBACK_DENYLIST`. Status/resolution follow **§4.1**: status from
`fields.status.statusCategory.key`, resolution from the inverted
`resolution_map`, plus the `default_reverse_resolution` / leave-untouched
precedence. `models.py` computes and validates the inverted `resolution_map` at
config load (**fails loudly on a non-injective map**). · *Tests:*
`test_jira_steps.py` — each category maps to the right BMO status; `new`
category on a resolved bug writes `REOPENED`, on an open bug writes `NEW`; an
unset Jira resolution falls through to the configured default; with no default,
status is written and resolution is left alone with a WARN; a planning-field
change writes nothing. `test_configuration.py` — a hand-crafted non-injective
`resolution_map` is rejected at load. · *Acceptance:* **PRD Scenario 3** (status
flows both directions) with **no reliance on inverting `status_map`.** ·
*Depends:* D6, D6b, D7, D8.

**D10 — Reverse comment writer + visibility guard (R-12).**
*Accomplishes:* comment sync from Jira to BMO, refused whenever either end is
confidential (Invariant D). Note for reviewers: v2 of this plan claimed the
guard "can never leak internal context onto a public bug" while specifying
only a check on the *BMO* side — it would have blocked writes to a restricted
bug while happily copying an embargoed Jira comment onto a public one. The
Jira-side checks and the forward-path private comment/attachment fixes close
that gap. · *Files:* `jbi/visibility.py` (guard from
`Bug.groups`/`is_private`), `jira_steps.py writeback_comment` with the
"from Jira, by \<name\>" attribution. · *Tests:* `test_visibility.py` +
`test_jira_steps.py` — a public bug receives the comment; a confidential/private
bug blocks write-back; attribution text is present. · *Acceptance:* **PRD
Scenario 4** and no internal context on a public bug. · *Depends:* D6, D6b, D7, D8.

**D11 — Conflict policy / execution-vs-planning (§4).**
*Accomplishes:* deterministic resolution when both sides changed, and centralized
write-back suppression. · *Files:* a central `WRITEBACK_DENYLIST` +
authoritative-source resolver used by all reverse writers. · *Tests:* a
same-window conflict resolves to the BMO value on execution fields; Sprint/Story
Points/Epic never write to BMO. · *Acceptance:* a Jira-only field edit never
touches BMO. · *Depends:* D9, D10.

**D12 — E2E harness + pilot enablement.**
*Accomplishes:* proves the four PRD acceptance scenarios end-to-end and turns the
pilot on. · *Files:* new `tests/e2e/` driving Scenarios 1–4 against a Jira sandbox
and a BMO test component; flip the pilot flag for Core :: Machine Learning:
On-Device. · *Tests:* Scenarios 1–4 within the 5-minute SLA; **Invariant A**
regression (re-syncing an already-linked bug creates zero new issues);
**Invariant C** regression (a Jira status change writes BMO once and the
resulting BMO webhook produces zero further Jira writes — the full round trip
terminates). ·
*Acceptance:* all four PRD §8 scenarios green. · *Depends:* D9–D11.

**D13 — Ingest seam (v4).**
*Accomplishes:* one entry point into the core for every transport, returning
an acknowledgement decision rather than just a result. · *Files:*
`jbi/ingest.py`; `router.py` routed through it. · *Tests:*
`test_ingest.py` — IGNORED acks; an unexpected failure asks for redelivery;
the webhook transport still uses the dead-letter queue while a broker
transport does not; duplicate message ids do no work twice; the dedupe cache's
eviction bound is asserted, not assumed. · *Acceptance:* existing webhook
behavior unchanged. · *Depends:* —

**D14 — Pub/Sub pull consumer (v5).**
*Accomplishes:* both sources delivered through the broker, with retry and
dead-lettering owned by the subscription, and explicit control over
concurrency and ordering. · *Files:* `jbi/consumer.py`, `jbi/__main__.py`
(`consume` command), `environment.py` (subscription settings). · *Tests:*
`test_consumer.py` — UTF-8/JSON/schema failures ack rather than retry;
`event_source` beats shape sniffing and an unknown value is rejected;
`delivery_id` is preferred over the broker message id; IGNORED acks; RETRY
and unexpected errors nack; flow control is never 1; the pull-window warning
names the abandoned-messages behavior; the shutdown race is matched
whichever way round the client words it. · *Acceptance:* an out-of-scope
event is never redelivered, and a backlog drains within the pull window. ·
*Depends:* D13.

**D15 — Sync-stop label (v4).**
*Accomplishes:* a human escape hatch that needs no deploy. · *Files:*
`models.py` (`sync_stop_label`), `writeback.py`, `runner.py`,
`jira_inbound/handler.py`, `jira_inbound/models.py` (`labels`). · *Tests:*
`test_sync_stop.py` — both directions stopped; removal resumes; case- and
whitespace-insensitive; creation unaffected; an absent `labels` key triggers
a fetch rather than reading as "no labels"; an action without the label
configured is untouched. · *Acceptance:* **Invariant E.** · *Depends:* —

**Dependency graph:** D1 → {D2, D3, D4→D5, D6, D6b, D8}; D7 standalone;
{D6, D6b, D7, D8} → D9 & D10 → D11 → D12. D13 → D14; D15 standalone.

---

## 10. Phases 2 & 3 (outline)

Phase 2 introduces the parts the PRD itself flagged as complex enough to defer,
once the flat bidirectional sync from Phase 1 is stable and trusted.

**Phase 2 — hierarchy, release data, identity, visibility**
- P2-1 metabug→epic (R-05); P2-2 bug-under-metabug→epic task (R-06);
  P2-3 re-parent protection + link preservation (R-07/R-08);
  P2-4 release flags + Target Milestone model & mapping (R-09/R-10), including
  confirming per-component field availability with BMO admins;
  P2-5 identity-map hardening (R-11); P2-6 visibility enforcement layer (R-12).

**Phase 3 — workflow accuracy & cleanup**
- P3-1 reconciliation report (R-13) plus surfacing unresolved identities;
  P3-2 spike into automatic delivery-epic attachment for newly-filed bugs
  (PRD §4.2), if a workable mechanism is found.

### 10.1 Metabug / epic mapping semantics (Phase 2)

This subsection specifies how BMO's metabug relationships map onto Jira's epic
hierarchy. It exists because the two models genuinely disagree, and leaving the
resolution implicit would force every reviewer to re-derive it.

**The tension (PRD §4).** In BMO a bug can block *any number* of metabugs — the
relationship is many-to-many, and metabugs serve double duty as functional-area
trackers (KTLO) and as feature/project planning buckets. In Jira a task has
*exactly one* parent epic. There is no lossless one-to-one mapping between the
two, and per the PRD this is a property to accommodate, not a flaw to fix (§3
non-goal: the integration "mirrors the relevant data … rather than forcing one
structure onto the other").

**Governing rule.** Ownership is split exactly as the domain table (§7) states,
and the mapping follows from it:

- **BMO is authoritative for the full many-to-many membership.** That membership
  (the bug's `Blocks`/`Depends-On` edges to metabugs) is mirrored into Jira
  *losslessly as issue links* to each metabug's mirror-epic. The complete graph
  is therefore always visible in Jira and always recoverable from BMO.
- **The Jira epic *parent* is a single, Jira-owned planning overlay.** JBI seeds
  it once, at task creation (R-06); thereafter humans re-organize freely in Jira
  and **JBI never re-parents a task from BMO.** Epic membership is Jira's to own
  (§7), so a later BMO-side change can add or remove a *link* but must never move
  the *parent*.

**Phase-1 relationship.** None of this applies in Phase 1: with no epics, metabug
membership is already mirrored as Jira "Blocks" issue links by the existing
`sync_dependencies` step (add and remove both handled), and there is no
one-parent constraint to conflict with. The rules below take effect only once
the epic layer (R-05/R-06) ships.

**Behavior matrix.**

| BMO situation | Jira result |
|---|---|
| Bug blocks one synced metabug | Task parented under that metabug's mirror-epic. |
| Bug blocks ≥2 synced metabugs | Task parented under **one** (deterministic default — lowest metabug bug-id, so re-runs don't flap); the other metabug(s) represented as **links** to their mirror-epics. Nothing lost. |
| Metabug membership **added** in BMO after sync | Add the corresponding **link**; the Jira parent is left untouched. |
| Metabug membership **removed** in BMO after sync | Remove the corresponding **link**; the Jira parent is left untouched (never yank a task out of a deliberately-chosen delivery epic). |
| Human re-parents the task in Jira (e.g. backlog → delivery epic) | Never writes back to BMO (R-07); the BMO origin link is preserved (R-08). |

**Why this is correct.** The many-to-many graph is preserved without loss (as
links), the single parent stays a planning decision Jira owns, and neither system
is forced onto the other's shape — which is precisely the PRD's stated non-goal.
BMO remains the recoverable source of truth for membership; Jira remains free for
planning.

**Open edge cases to confirm during Phase 2 design:**
- **Tie-break** for the seeded parent when a bug blocks multiple synced metabugs
  (proposed: lowest metabug bug-id) — confirm this is the desired default.
- **Seeded parent metabug removed in BMO:** leave the Jira parent as-is (Jira owns
  it), drop the stale link. Confirm.
- **Metabug leaves synced scope** (its Product/Component is changed): proposed —
  stop syncing it, leave the existing mirror-epic and links intact rather than
  deleting planning structure. Confirm.
- **Mirror-epic lifecycle:** is a mirror-epic created eagerly for every metabug in
  a synced component, or lazily on first child sync? (Affects epic clutter.)

---

## 11. Testing strategy

The bar is senior-review quality and zero regressions, so testing is specified
per deliverable rather than deferred to the end.

- **Every PR includes** unit tests for its new lines (the repo enforces a 75%
  coverage floor via `make test`), a green run of the full existing suite, and a
  clean `make lint` (ruff format + ruff check + mypy + bandit + detect-secrets +
  yamllint + the actions-config lint, per `bin/lint.sh`).
- **Reuse the existing harness** rather than inventing test infrastructure:
  `factory-boy` factories for every model (we add a `JiraWebhookEventFactory`, and
  a `with_release_flags` trait on `BugFactory` for Phase 2); the autouse
  `mocked_jira` / `mocked_bugzilla` fixtures; `responses` for HTTP-level
  behavior; the `TestClient` via `authenticated_client`; a file-backed `dl_queue`
  on `tmp_path`; and the `no_mocked_*` markers for the few contract-level tests.
- **New test modules mirror the source layout:** `tests/unit/jira_inbound/`,
  `tests/unit/test_jira_steps.py`, `tests/unit/test_identity.py`,
  `tests/unit/test_visibility.py`, and a new `tests/e2e/`.
- **The invariant tests are permanent regression guards:** Invariant A (no
  duplicate Jira issue on re-sync), Invariant B (no BMO bug from an inbound
  event), and **Invariant C (no echo in either direction)** live in the suite so
  no future change can silently break them. Invariant C is tested at three
  levels: unit (bot-authored event → IGNORE, D6b), unit (write of an unchanged
  value → no API call, D7), and e2e (a full Jira→BMO→Jira round trip terminates
  with exactly one BMO write and zero return Jira writes, D12).
- **Round-trip fidelity is tested explicitly, not assumed:** for every Jira
  `statusCategory` the reverse map produces a BMO status which, pushed back
  through the *forward* `status_map`, must land on a Jira status in the same
  category. A property-style test over each configured action's `status_map`
  catches a project whose workflow would oscillate before it is onboarded, and is
  the mechanical check behind §4.1's claim that lower-fidelity reverse mapping is
  safe.
- **Reviewability is a design goal of the test plan:** because each PR is
  additive and flag-off, it can be merged without changing production behavior,
  and the reverse writers stay disabled until both D6's correlation/echo gate and
  D7's idempotency are merged — a reviewer never has to evaluate a reverse write
  landing without its safety machinery already present.

---

## 12. Operational setup

What has to be configured outside the code for the pilot to work. Included so
reviewers and admins can see the full surface, not just the code.

**Project routing (how a bug reaches the right Jira project).** BMO does not name
a Jira project; JBI derives it. Two layers: (1) a Bugzilla **webhook registered on
the pilot Product/Component** determines which bugs are sent to JBI at all; (2) the
bug's **`whiteboard` tag** is matched in JBI config, and each action maps one tag
→ one `jira_project_key`. So a bug in the synced component with `[ml-ondevice]`
in its whiteboard is created in the configured project (e.g. `AIPLAT`). The
reverse direction needs no routing config — it correlates an inbound issue back to
its bug via the existing link.

**Jira — already required today (forward sync), to verify for the pilot project:**
- Add the **Jira Automation Bot** to the project with **`CREATE_ISSUES`,
  `EDIT_ISSUES`, `ADD_COMMENTS`, `DELETE_ISSUES`** (delete backs the
  duplicate-cleanup step).
- Grant **"Browse users and groups"** (global) so assignee resolution works.
- Ensure the fields the steps write (status transitions, priority, severity/points
  custom fields, components) exist on the create/update screens.

**Pub/Sub — the transport (v4).** One topic carries both sources; JBI reads
one push subscription.
- **Topic** with both publishers: the Bugzilla side (a relay, or BMO itself if
  it can publish) and the Jira Automation rule.
- **Pull subscription** consumed by `python -m jbi consume`, deployed as its
  own unit (a Cloud Run job or a second service) beside the web app. It
  authenticates with application default credentials and needs
  `roles/pubsub.subscriber`; there is no inbound HTTP and therefore no shared
  secret in a URL.
- **Pull window** (`PUBSUB_PULL_TIMEOUT_SECONDS`, default 540) must stay below
  both the lease duration and any Cloud Run job task timeout, so the process
  exits cleanly instead of being killed mid-message.
- **Concurrency** (`PUBSUB_MAX_CONCURRENT_MESSAGES`, default 10) must never be
  1: one slot serialises every ordering key, so a backlog cannot drain before
  the window closes and held ordered messages are stranded.
- **Retry policy**: exponential backoff, and a **dead-letter topic** with a
  max-delivery-attempts limit. This replaces JBI's file-based dead-letter
  queue for broker-delivered events, and resolves two v3 limitations — the
  queue could not hold Jira events, and it assumed a single instance.
- **Ordering**: enable message ordering on the subscription with an ordering
  key of the **bug id**. Without it, a status change and a comment on the
  same bug can arrive out of order — not fatal, since each write is
  independent and idempotent, but it makes the reverse conflict rule less
  reliable, because "the previous value" assumes events arrive in sequence.
  With pull, ordering and concurrency interact: per-key order is preserved
  while different keys process in parallel.
- **Message attributes**: publishers should set `event_source` to `bugzilla`
  or `jira`, and a stable `delivery_id` (preferred over the broker message id
  for duplicate suppression, because it survives a redelivery). JBI falls back to payload-shape detection, but an explicit
  attribute is what keeps a future third payload shape from being guessed at.
- IAM: the consumer's service account needs `roles/pubsub.subscriber` on the
  subscription.

**The sync-stop label (v4).**
- Choose one label name per action (`sync_stop_label`, eg. `jbi-sync-stop`)
  and **document it wherever the team is told how JBI works** — an escape
  hatch nobody knows about is not an escape hatch.
- It is unset by default, so no project has it until configured.

**Jira — new for bidirectional (this project):**

> **Who publishes Jira events? Confirmed as an onboarding question by live
> testing.** The sandbox used a per-project Automation rule, which is the
> thing review already rejected: it does not scale, and it means asking every
> team to build an automation. Three ways to avoid that, in order of
> preference:
>
> 1. **A site-level Jira webhook with a JQL scope** — one admin config for
>    every project, and it sends Jira's standard webhook payload, which
>    **includes the changelog natively**. Automation's built-in body does
>    not (see D14's findings), so this also removes the custom-body
>    requirement below. Needs a Jira site admin; not self-service.
> 2. **One central multi-project Automation rule** (the v3 decision) — needs
>    a plan that supports multi-project rule scope (§13-7).
> 3. **A Jira → Pub/Sub bridge**, if the org already runs one.
>
> Whichever is chosen, onboarding a team must mean editing a JQL clause and
> adding a `config.yaml` entry — never asking that team to configure Jira.
> **The custom request body documented below is only needed for options 2
> and 3**, because Automation is the producer that omits the changelog.

- **One centrally-owned, multi-project Automation rule** — *not* one rule per
  project. *When an issue's status, assignee, priority, or summary changes, or a
  comment is added → **Send web request*** to
  `POST https://<jbi-host>/jira_webhook` with the JBI API key.
  - **Scope:** created at the site/global level with rule scope set to *multiple
    projects*, and narrowed by a **JQL condition** listing the opted-in projects,
    e.g. `project in (AIPLAT)` for the pilot. Onboarding a project is then a
    **one-line JQL edit** by the rule's owner, reviewed like any other config
    change — not a request to that project's Jira admin to hand-build a rule.
  - **Why not per-project:** prod config already spans **35 Jira projects across
    43 actions** (`config/config.prod.yaml`). Per-project rules would mean 35
    independently-owned copies with no central answer to "which projects are
    wired up, and is each still configured correctly?" — the same drift problem
    that config-in-repo exists to avoid. One rule makes the opted-in set a single
    readable JQL clause, and revocation a single edit.
  - **Ownership:** the rule is owned by the JBI service account in a
    JBI-administered space, so it cannot be silently edited or deleted by an
    individual project's admins.
  - **Keep it aligned with repo config:** the JQL project list must match the
    projects opted into inbound sync in `config/config.{env}.yaml`. Phase 3 adds
    a drift check to the R-13 reconciliation report (rule scope vs. config); until
    then, the pairing is a documented step in the onboarding checklist.
  - **Prerequisite / fallback:** multi-project rule scope requires Jira Cloud
    **Premium** (single-project rules are available on all plans). **To verify
    before D6** (§13-7). If the site is not on Premium, the fallback is a
    per-project rule for the **pilot only**, with the multi-project rule treated
    as a blocker to onboarding project #2 — we do not onboard the other 34
    projects by hand.
- Standardize on a single JBI service account for Jira writes, so inbound
  events it authored can be suppressed for loop-prevention (Invariant C). Record
  its `accountId` as `Settings.jira_bot_account_id`.
- The BMO side of that pair already exists in production: JBI's Bugzilla writes
  are authored by **`jira-integration@bots.tld`** (confirmed on the `see_also`
  of a live pilot-component bug), which is the value for
  `Settings.bugzilla_bot_login`. Note that Phabricator-driven changes arrive as
  `phab-bot@bmo.tld`, a *different* account, and must keep flowing.
- Confirm the service account can read user **email** (identity resolution, §5).
- Confirm the rule's webhook payload includes **`fields.status.statusCategory`**,
  which the reverse status mapping depends on (§4.1) — include it explicitly in
  the rule's request body rather than relying on the default payload shape.
- Phase 2: custom fields for **release flags** and **Target Milestone**, and the
  **Epic** issue type available.

**Bugzilla — new for reverse write-back:**
- JBI's Bugzilla API-key account must have **edit permission on bugs** in the
  pilot component (today it only writes the `see_also` link). Its comments post
  under that account, which is why reverse comments are text-attributed rather
  than impersonated.
- Confirm the per-Product/Component **webhook** is registered so bugs reach JBI.
- Record the login of JBI's Bugzilla account as `Settings.bugzilla_bot_login`,
  so the forward-path echo gate can recognize JBI's own reverse writes
  (Invariant C, D6b). Deploying the reverse writers without this set is the one
  configuration mistake that reintroduces the loop, so D12's pilot-enablement
  checklist asserts it is non-empty before the pilot flag is flipped.

---

## 13. Risks / to-verify

1. **Jira email visibility** on the deployed target instance (confirmed for
   `mozilla-hub`; verify it is the same instance or configured to expose email to
   JBI's account). Impacts §5 tier-2 resolution.
2. **Jira Automation egress** must be permitted from the pilot project (the
   inbound rule depends on it).
3. **Terminal-state handling** for Phabricator transitions — never move a closed
   issue back to In Review (D4/D5).
4. **Release-flag field shape** in BMO for the pilot — confirm the exact
   `cf_status_firefox*` fields (D-P2-4).
5. **Multi-instance state** — the file-based dead-letter queue (`jbi/queue.py`)
   effectively assumes a single instance today. This is pre-existing and out of
   scope, but revisit if the service is scaled out (it also bears on where any
   future shared loop-prevention state would live).
6. **Comment attribution format** for BMO write-back — confirm the exact wording
   with stakeholders.
7. **Jira Cloud plan tier** — multi-project Automation rule scope requires
   Premium. Blocks the central-rule design in §12; verify before D6. Fallback is
   a pilot-only single-project rule, treated as a blocker to onboarding a second
   project rather than a licence to hand-build 35 rules.
8. **`statusCategory` in the Automation payload** — confirm the "Send web
   request" body carries `fields.status.statusCategory.key`; the entire reverse
   status mapping (§4.1) depends on it. Cheap to verify with one test rule.
9. **`resolution_map` injectivity** — true for all 17 prod configs that define
   one today, and enforced at config load going forward (D9). The risk is a
   future project wanting a genuinely many-to-one resolution map; it would fail
   validation and need an explicit reverse override, which is the intended
   loud-failure behavior rather than a silent wrong write.
10. **Confidentiality field shapes** — *resolved, both halves.*
    **Jira:** the built-in Automation body sends `security: null`; a custom
    body renders an unset level as `{"name": ""}`, which the inbound models
    now normalise to absent — without that, every issue would read as
    embargoed and all free-text write-back would be blocked.
    **BMO:** a group-restricted bug's webhook payload carries
    `is_private: true`, **omits `groups` entirely**, and redacts the summary
    to null — while the REST API, queried as a group member, reports the
    same bug as `is_private: None` with `groups` populated. Neither signal
    alone covers both sources, which is why the guard checks both. Note BMO
    redacting the payload does not make the guard optional: JBI re-fetches
    the bug as its own account, which may belong to the group, so the
    rejection has to happen before the refresh.
11. **Duplicate suppression is in-process only.** The cache is bounded and
    per-process, so two consumer replicas do not share it and a restart
    forgets it. Field writes are idempotent regardless; the exposure is a
    *redelivered comment* posting twice across replicas. The reference
    implementation solves this with a Firestore-backed idempotency service
    keyed on `delivery_id` with a 24h TTL — the right shape, and the obvious
    next step if JBI runs more than one consumer. Until then, run a single
    consumer replica and keep the ack deadline generous enough that
    redelivery is rare.
12. **Out-of-order delivery (v4).** Without an ordering key, Pub/Sub may
    deliver a bug's events out of sequence, which weakens D11's conflict rule
    (it compares against "the value before this change"). Set an ordering key
    on the bug id if the publisher can.
13. **Actor-check coverage on the Bugzilla side** — `WebhookEvent.user` is
    `Optional`, so the echo gate cannot fire on an actor-less event. Mitigated
    by D7 read-before-write; worth confirming with BMO which event classes can
    legitimately arrive without a `user`.

---

## 14. Open questions (from PRD §9)

- **Pilot scope** — confirmed: Core :: Machine Learning: On Device.
- **Conflict resolution policy** — confirmed: BMO wins for execution fields (§4).
- **Story Points / Iteration availability per Component** — confirm with BMO
  admins during the Phase 2 field-mapping work; determines whether these are
  Jira-only (write-back-suppressed) or genuinely bidirectional.
- **Reverse status fidelity** — is three-category granularity (§4.1) acceptable
  to the pilot team, or do they want per-project `reverse_status_overrides` from
  day one? Proposed: ship on the defaults, add overrides only when a project
  demonstrates a need.
- **Default reverse resolution** — confirm `FIXED` is the right
  `default_reverse_resolution` for the pilot, versus leaving resolution untouched
  and routing every green-category close to the reconciliation report.
- **Ownership of the central Automation rule** — which team/service account
  administers it, and what the change process is for adding a project to its JQL
  scope. This is now a shared operational asset rather than each project's own
  config.
- **Relationship to existing JBI automation** — extend, do not replace
  (constraint #6); Phase 1 confirms what can be extended before building anything
  parallel.
