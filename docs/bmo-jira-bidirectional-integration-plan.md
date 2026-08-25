# BMO ↔ Jira Bidirectional Integration — Engineering Plan

> **Status:** DRAFT for engineering review.
> **Purpose:** Translate the "BMO – Jira Integration" PRD (v2) into a concrete,
> reviewable engineering plan: what exists in JBI today, what the requirements
> ask for, where the two diverge, and a phased, testable path to close the gap.
> **Audience:** Reviewers and implementers. This document does not itself change
> behavior; it is the design and traceability reference the implementation PRs
> will point back to.

---

## 1. Decisions locked

These decisions were settled with stakeholders and constrain everything below.
They are stated with their reasoning so reviewers can challenge the *reasoning*
rather than infer it.

| Area | Decision |
|---|---|
| Inbound Jira path | **Push** via a Jira Automation "Send web request" rule → a new `POST /jira_webhook` endpoint. Polling is a documented fallback only. |
| State store | **No new datastore in Phase 1.** Correlation reuses the existing `see_also`/remote-link; identity lives in YAML; loop-prevention is stateless. Redis is considered only if multi-instance ephemeral state later proves necessary. |
| Conflict policy | **BMO wins for execution fields; Jira wins for planning-only fields.** (Defined in §4.) |
| Pilot | **Core :: Machine Learning: On-Device.** |
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
   behind config and is enabled only for Core :: Machine Learning: On-Device, so
   production impact is contained and a problem is contained with it.
5. **Reuse over rebuild.** Where the PRD overlaps existing behavior, we extend it:
   R-02/R-03 build on `maybe_add_phabricator_link`; Jira→BMO close reuses the
   existing status/resolution maps; the reconciliation job reuses the standalone
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
unchanged. The reverse path is guarded at three points before any write: it
correlates the issue back to a bug (Invariant B), suppresses events authored by
JBI's own service account (loop-prevention), resolves identities and enforces
visibility, and applies the write-back denylist (§4).

```
Bugzilla --POST /bugzilla_webhook--> execute_or_queue --> Executor --> steps.py -----> Jira
   ^                                       |                                  |
   |                               DeadLetterQueue                     (unchanged)
   |
   +-- bugzilla_service writes <- jira_steps.py <- ReverseExecutor <- execute_or_queue <- POST /jira_webhook <- Jira Automation
       (status/assignee/priority/                       |                     ^
        summary/comment, guarded)             identity + visibility      (echo-suppressed:
                                              + writeback denylist        ignore JBI-bot actor;
                                                                          ignore uncorrelated issue)
```

**New modules** — kept as new files so the diff is legible and the forward path is
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
| **Idempotency / loop prevention** | ❌ | Service-account echo-suppression + read-before-write idempotency. Stateless (no store). |
| **R-05** metabug→epic | ❌ | `jbi/hierarchy.py` + `jira/service.py create_epic/find_epic`; step `ensure_metabug_epic`. |
| **R-06** bug-under-metabug→epic task | ❌ | `hierarchy.py`: on CREATE, parent under the metabug's epic where applicable. |
| **R-07** re-parent never writes to BMO | 🟡 enforce | Add epic/parent to `WRITEBACK_DENYLIST`; reverse steps ignore parent-change events. |
| **R-08** BMO link preserved after re-parent | ✅ / guard | Existing `see_also` + remote link (`extract_from_see_also`, `add_link_to_bugzilla`); assert untouched by re-parent. |
| **R-09** release flags → Jira | ❌ | Type `cf_status_firefoxNN` on `Bug`; step `mirror_release_flags`. |
| **R-10** Target Milestone → Jira | ❌ | Add `target_milestone` to `Bug`; step `mirror_target_milestone`. |
| **R-11** identity matching | ❌ | `jbi/identity.py` + `config/identity_map.{env}.yaml` + `bin/seed_identity_map.py` (§5). |
| **R-12** visibility on write-back | ❌ | `jbi/visibility.py` guard from `Bug.groups`/`is_private`. |
| **R-13** reconciliation report | ❌ | `jbi/reconcile.py` standalone job (retry.py pattern). |

---

## 9. Phase 1 — testable deliverables (PR-sized)

**Sequencing philosophy.** The forward-direction items (D2–D5) are independent
and low-risk, so they can land in any order after the scaffolding. The
reverse-direction items are strictly ordered so that **no reverse write can ever
land before the safety machinery that governs it**: the inbound spine and its
correlation/echo gate (D6) and idempotent writes (D7) merge *before* any field
writer (D9/D10) is enabled. Each deliverable is additive, config-gated, and
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

**D7 — BMO write service + idempotent writes.**
*Accomplishes:* the low-level ability to write execution fields back to BMO,
built so that an echoed value is a silent no-op — the second half of
loop-prevention. · *Files:* `bugzilla/service.py` new methods
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
`WRITEBACK_DENYLIST`. · *Tests:* `test_jira_steps.py` — each event maps to the
right BMO write; a planning-field change writes nothing. · *Acceptance:* **PRD
Scenario 3** (status flows both directions). · *Depends:* D6, D7, D8.

**D10 — Reverse comment writer + visibility guard (R-12).**
*Accomplishes:* comment sync from Jira to BMO that can never leak internal
context onto a public bug. · *Files:* `jbi/visibility.py` (guard from
`Bug.groups`/`is_private`), `jira_steps.py writeback_comment` with the
"from Jira, by \<name\>" attribution. · *Tests:* `test_visibility.py` +
`test_jira_steps.py` — a public bug receives the comment; a confidential/private
bug blocks write-back; attribution text is present. · *Acceptance:* **PRD
Scenario 4** and no internal context on a public bug. · *Depends:* D6, D7, D8.

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
regression (re-syncing an already-linked bug creates zero new issues). ·
*Acceptance:* all four PRD §8 scenarios green. · *Depends:* D9–D11.

**Dependency graph:** D1 → {D2, D3, D4→D5, D6, D8}; D7 standalone;
{D6, D7, D8} → D9 & D10 → D11 → D12.

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
  duplicate Jira issue on re-sync) and Invariant B (no BMO bug from an inbound
  event) live in the suite so no future change can silently break them.
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

**Jira — new for bidirectional (this project):**
- A **Jira Automation rule** on the pilot project: *When an issue's status,
  assignee, priority, or summary changes, or a comment is added → **Send web
  request*** to `POST https://<jbi-host>/jira_webhook` with the JBI API key. This
  rule *is* the inbound push mechanism.
- Standardize on a single JBI service account for Jira writes, so inbound
  events it authored can be suppressed for loop-prevention.
- Confirm the service account can read user **email** (identity resolution, §5).
- Phase 2: custom fields for **release flags** and **Target Milestone**, and the
  **Epic** issue type available.

**Bugzilla — new for reverse write-back:**
- JBI's Bugzilla API-key account must have **edit permission on bugs** in the
  pilot component (today it only writes the `see_also` link). Its comments post
  under that account, which is why reverse comments are text-attributed rather
  than impersonated.
- Confirm the per-Product/Component **webhook** is registered so bugs reach JBI.

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

---

## 14. Open questions (from PRD §9)

- **Pilot scope** — confirmed: Core :: Machine Learning: On-Device.
- **Conflict resolution policy** — confirmed: BMO wins for execution fields (§4).
- **Story Points / Iteration availability per Component** — confirm with BMO
  admins during the Phase 2 field-mapping work; determines whether these are
  Jira-only (write-back-suppressed) or genuinely bidirectional.
- **Relationship to existing JBI automation** — extend, do not replace
  (constraint #6); Phase 1 confirms what can be extended before building anything
  parallel.
