# BMO ↔ Jira Bidirectional Integration — Engineering Plan

> **Status:** DRAFT for review · **Scope:** Expands JBI from one-directional
> (Bugzilla → Jira) sync into bidirectional, hierarchy-aware sync per the
> "BMO – Jira Integration" PRD (v2). This document is the engineering plan and
> traceability map; it does not itself change behavior.

---

## 1. Decisions locked

| # | Decision |
|---|---|
| Inbound Jira path | **Push** via Jira Automation "Send web request" → new `POST /jira_webhook`. Polling is a documented fallback only. |
| State store | **None new for Phase 1.** Correlation via existing `see_also` / remote-link; identity via YAML; loop-prevention stateless. Redis only if multi-instance ephemeral state is later proven necessary. |
| Conflict policy | **BMO wins for execution fields; Jira wins for planning-only fields.** (Defined in §4.) |
| Pilot | **Core :: Machine Learning: On-Device.** |
| Identity map | **Email-first automatic resolution + machine-seeded YAML override file for mismatches only + reconciliation-driven upkeep.** (§5.) |
| Scope of change | **Extend only. Nothing existing may break.** All new behavior is additive, config-gated, default-OFF, opted into only by the pilot. (§6.) |
| Build vs. integrate | **Integration path chosen.** Recorded via ADR `docs/adrs/004-bidirectional-sync.md`. |

---

## 2. Problem statement (summary)

JBI today is a one-directional pipeline: Bugzilla → Jira. A single inbound
endpoint (`jbi/router.py` `/bugzilla_webhook`) feeds an `Executor`
(`jbi/runner.py`) that runs config-selected step functions (`jbi/steps.py`),
all of which write to Jira. The only value ever written back to BMO is the
`see_also` link (`jbi/bugzilla/service.py add_link_to_see_also`).

The PRD requires **bidirectional field sync**, **metabug→epic hierarchy**, and
**release / identity / visibility** handling. The reverse (Jira → BMO) direction
is the keystone; most Phase-1 reverse work depends on it.

---

## 3. Invariants (all phases obey)

**Invariant A — never create a duplicate Jira issue.** When a BMO bug already
links to a Jira issue (via `see_also`), sync UPDATEs it; it never creates a
second one. *Already enforced today:* `runner.py do_execute_actions` routes
linked bugs to UPDATE, and the "tag added to an already-linked bug" case flows
through `steps.py create_issue`, which detects the existing issue and updates
instead of creating. This must be preserved and covered by a regression test at
rollout, since the pilot already has many linked bugs.

**Invariant B — Jira → BMO is write-back only; it never creates a BMO bug.**
BMO is authoritative for *what work exists* (PRD §2). The reverse executor
resolves the linked BMO bug from the inbound Jira issue (Bugzilla remote link →
`see_also` fallback); **if no linked bug exists, the event is ignored.** A
standalone Jira issue (cloud/service work with no bug) has no reverse effect.

**Direction asymmetry (consequence):** BMO → Jira may CREATE-or-UPDATE;
Jira → BMO is UPDATE-only. The same correlation gate that enforces Invariant B
also scopes loop-prevention — we only write back to bugs we already hold the
link for.

---

## 4. Field ownership & conflict policy

**Execution fields** — *what the work is and its technical state.*
**BMO authoritative; on a same-window conflict BMO wins and Jira is overwritten.**

- Bidirectional, BMO-wins: **Summary, Status/Resolution, Assignee, Priority, Severity.**
- BMO-owned structural data, **mirrored to Jira but never written back from Jira:**
  Product & Component, Blocks/Depends-On (metabug structure), Release flags,
  Target Milestone, Flags & Keywords.

**Planning fields** — *how work maps to delivery.*
**Jira authoritative; never written back to BMO** (Phase-1 "write-back suppression"):
Epic membership/parent, Sprint, Milestones, Story Points / RICE (where BMO lacks
the field), roadmap / project org.

Implementation: a single `WRITEBACK_DENYLIST` (planning fields) consulted by
every Jira → BMO step, plus a per-field "authoritative source" used only to
resolve same-window conflicts.

---

## 5. Identity map design (R-11)

- **Resolution order at runtime:** (1) YAML override map → (2) automatic email
  lookup (`jira/service.py find_jira_user`) → (3) fallback: leave/clear assignee,
  attribute comments in text. Never guess; never drop a comment.
- **Map = exceptions only** (email mismatches / hidden-email users). The common
  case, including new hires with a consistent corporate email, resolves
  automatically with no map entry.
- **Storage:** new global file `config/identity_map.{env}.yaml`, separate from
  the `Actions` list so existing config parsing is untouched. Jira side keyed on
  **accountId** (stable), not email.
- **Seeding:** `bin/seed_identity_map.py` bulk-pulls the Jira user directory
  (`email → accountId`) so the file is machine-generated, not hand-typed; detects
  drift.
- **Upkeep:** unresolved *active* users are logged and surfaced in the R-13
  reconciliation report — alert-driven, not manual polling.
- **No impersonation:** comment write-back always posts as the JBI service
  account with a "from Jira, by \<name\>" prefix; the map supplies the *name* only.
- **Sentinel:** BMO `nobody@mozilla.org` (`bug.is_assigned()`) = unassigned.
- **To verify:** the JBI-target Jira instance exposes email to the service
  account (confirmed for mozilla-hub; confirm it is the same instance).

---

## 6. Cross-cutting constraints ("extend, don't break")

1. New sync behaviors are **additive step functions**, config-gated, **default-OFF**.
2. `/jira_webhook` is a **separate** endpoint; `/bugzilla_webhook` is untouched.
3. All new model fields are **optional with safe defaults**; every existing
   `config/config.*.yaml` keeps parsing.
4. Existing test suite stays green; new behavior ships behind config; only the
   pilot opts in.
5. Reuse over rebuild: R-02/R-03 extend `maybe_add_phabricator_link`; Jira → BMO
   close reuses the existing status/resolution maps; reconciliation reuses the
   `jbi/retry.py` standalone-job pattern.

---

## 7. Architecture

### Current (as-is)

```
Bugzilla --POST /bugzilla_webhook--> execute_or_queue --> Executor --> steps.py --> Jira
                                          |                                |
                                   DeadLetterQueue                   (many writes)
                                                                          |
                                                                   BMO write: see_also link only
```

### Target

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

**New modules:** `jbi/jira_inbound/` (event models + endpoint), `jbi/jira_steps.py`
(reverse steps), `jbi/identity.py`, `jbi/visibility.py`, `jbi/hierarchy.py`
(Phase 2), `jbi/reconcile.py` (Phase 3), `bin/seed_identity_map.py`.

**Extended:** `jbi/bugzilla/service.py` (write methods), `jbi/bugzilla/models.py`
(release flags + target milestone), `jbi/models.py` (`ActionParams`:
scope/threshold/identity/inbound toggles).

---

## 8. Requirement → change map

Legend: ✅ Satisfied · 🟡 Partial (foundation exists) · ❌ Missing

| Req | Verdict | Files / functions to add or extend |
|---|---|---|
| **R-01** scope by Product/Component | 🟡 extend | `models.py ActionParams` add `sync_products_components`; gate in `runner.py lookup_actions` / `do_execute_actions`; verify BMO webhook registration for pilot (`bugzilla/service.py check_bugzilla_webhooks`). |
| **R-02** patch opened → In Review | 🟡 extend | `steps.py maybe_add_phabricator_link` + `jira/service.py update_issue_status` + `jira/client.py get_issue_transitions_with_fields`. Skip if terminal. |
| **R-03** "requires changes" → out of In Review | ❌ (data exists) | Read `AttachmentFlag{name,value}`; new step `sync_phabricator_review_state`. |
| **R-04** priority/severity threshold | ❌ | `ActionParams` `min_priority`/`min_severity`; early-ignore gate in `runner.py`. |
| **Field sync BMO→Jira** | ✅ done | Existing steps: summary/status/assignee/priority/comment. No change. |
| **Field sync Jira→BMO** (§6.2 table) | ❌ | `jira_steps.py` writers + `bugzilla/service.py` new methods over `client.update_bug`. |
| **Idempotency / loop prevention** | ❌ | Service-account echo-suppression + read-before-write idempotency. Stateless. |
| **R-05** metabug→epic | ❌ | `jbi/hierarchy.py` + `jira/service.py create_epic/find_epic`; step `ensure_metabug_epic`. |
| **R-06** bug-under-metabug→epic task | ❌ | `hierarchy.py`: on CREATE, parent under metabug's epic if applicable. |
| **R-07** re-parent never writes to BMO | 🟡 enforce | Add epic/parent to `WRITEBACK_DENYLIST`; reverse steps ignore parent changes. |
| **R-08** BMO link preserved | ✅/guard | Existing `see_also` + remote link (`extract_from_see_also`, `add_link_to_bugzilla`). |
| **R-09** release flags → Jira | ❌ | Type `cf_status_firefoxNN` on `Bug`; step `mirror_release_flags`. |
| **R-10** Target Milestone → Jira | ❌ | Add `target_milestone` to `Bug`; step `mirror_target_milestone`. |
| **R-11** identity matching | ❌ | `jbi/identity.py` + `config/identity_map.{env}.yaml` + `bin/seed_identity_map.py`. |
| **R-12** visibility on write-back | ❌ | `jbi/visibility.py` guard from `Bug.groups`/`is_private`. |
| **R-13** reconciliation report | ❌ | `jbi/reconcile.py` standalone job (retry.py pattern). |

---

## 9. Phase 1 — testable deliverables (PR-sized)

Each is **additive, config-gated, default-OFF**, lands with its own tests, and
keeps the full suite green. Format: *Requirement · Files · Tests · Acceptance ·
Depends-on · Review notes.*

### Forward-direction (independent, low-risk)

**D1 — Scaffolding & config model.**
- *Req:* enabler for constraint #4.
- *Files:* `jbi/models.py` (`ActionParams`: optional `sync_products_components`,
  `min_priority`, `min_severity`, `identity_map_enabled`, `jira_inbound_enabled`,
  all default no-op); ADR `docs/adrs/004-bidirectional-sync.md`.
- *Tests:* `tests/unit/test_models.py`, `test_configuration.py` — existing
  configs still parse; defaults reproduce current behavior.
- *Acceptance:* zero behavior change with default config.
- *Depends:* — · *Review:* pure additive schema; small, obviously safe.

**D2 — Product/Component scope gate (R-01).**
- *Files:* `runner.py do_execute_actions` / `lookup_actions`.
- *Tests:* `test_runner.py` with `WebhookRequestFactory`/`BugFactory` — in-scope
  bug syncs; out-of-scope bug produces no Jira call.
- *Acceptance:* **PRD Scenario 2.** · *Depends:* D1.

**D3 — Priority/severity threshold (R-04).**
- *Files:* `runner.py` gate.
- *Tests:* `test_runner.py` — sub-threshold → no create; at/above → create.
- *Acceptance:* below-threshold bug creates nothing. · *Depends:* D1.

**D4 — Phabricator patch-opened → In Review (R-02).**
- *Files:* `steps.py maybe_add_phabricator_link`; `jira/service.py`
  `update_issue_status` + `client.get_issue_transitions_with_fields`.
- *Tests:* `test_steps.py` with `context_attachment_example` +
  `WebhookAttachmentFactory` — patch-open transitions to In Review; terminal
  issue skipped.
- *Acceptance:* patch opened → Jira In Review. · *Depends:* D1.

**D5 — Phabricator "requires changes" → out of In Review (R-03).**
- *Files:* new `steps.py sync_phabricator_review_state` reading `AttachmentFlag`.
- *Tests:* `test_steps.py` — `review-` transitions out; `review+`/`review?` do not.
- *Acceptance:* requires-changes moves out of In Review. · *Depends:* D4.

### Reverse-direction (sequential; loop-safety intrinsic)

**D6 — Jira inbound spine (infra).**
- *Traces to:* PRD Phase-1 "Bidirectional field sync"; enabling infra for PRD
  §6.2 Jira→BMO table + Acceptance Scenarios 3 & 4 (not an R-number itself).
- *Files:* `jbi/jira_inbound/models.py`, `router.py` (new `POST /jira_webhook`,
  reuse `api_key_auth`), `jbi/jira_steps.py` (ReverseExecutor skeleton), reuse
  `DeadLetterQueue`. Handler correlates issue→bug and ignores if (a) no linked
  bug [Invariant B] or (b) event authored by the JBI service account [loop gate].
  **No writes yet.**
- *Tests:* `tests/unit/jira_inbound/` + `test_router.py` — auth parity (401
  without key); valid event enqueues/acks; uncorrelated issue → no-op;
  bot-authored event → no-op.
- *Acceptance:* **Invariant B** (uncorrelated inbound → no BMO write, no bug creation).
- *Depends:* D1 · *Review:* no behavioral risk — endpoint only logs/correlates;
  mirrors the proven `/bugzilla_webhook`.

**D7 — BMO write service + idempotent writes.**
- *Files:* `bugzilla/service.py` new methods (`set_status_resolution`,
  `set_assignee`, `set_priority`, `set_summary`, `add_comment`) over existing
  `client.update_bug`; read-before-write (reuse `refresh_bug_data`) so an echo
  write is a no-op.
- *Tests:* `test_service.py` with `responses`/mocked client — each writer maps
  correctly; write skipped when value already matches (loop-safety unit proof).
- *Acceptance:* round-trip write with unchanged value issues no BMO update.
- *Depends:* — (service layer, unwired) · *Review:* isolated, unit-tested.

**D8 — Identity map (R-11).**
- *Files:* `jbi/identity.py`, `config/identity_map.{env}.yaml`,
  `bin/seed_identity_map.py`; wire into forward `maybe_assign_jira_user` first.
- *Tests:* `test_identity.py` — map hit; email-fallback; unresolved fallback;
  `nobody@mozilla.org` sentinel; seed script vs mocked Jira user directory.
- *Acceptance:* mismatched-email user resolves via map; unmapped resolves by
  email; unresolved degrades gracefully. · *Depends:* D1.

**D9 — Reverse field writers (§6.2 Jira→BMO).**
- *Files:* `jira_steps.py` — `writeback_status`/`_resolution`, `_priority`,
  `_assignee` (uses D8), `_summary`; behind `jira_inbound_enabled`; enforces
  `WRITEBACK_DENYLIST`.
- *Tests:* `test_jira_steps.py` — each event maps to the right BMO write;
  planning-field change writes nothing.
- *Acceptance:* **PRD Scenario 3** (status both directions). · *Depends:* D6, D7, D8.

**D10 — Reverse comment writer + visibility guard (R-12).**
- *Files:* `jbi/visibility.py` (guard from `Bug.groups`/`is_private`),
  `jira_steps.py writeback_comment` with "from Jira, by \<name\>" attribution.
- *Tests:* `test_visibility.py` + `test_jira_steps.py` — public bug gets comment;
  confidential/private bug blocks write-back; attribution text present.
- *Acceptance:* **PRD Scenario 4** + no internal context on a public bug.
- *Depends:* D6, D7, D8.

**D11 — Conflict policy / execution-vs-planning (§4).**
- *Files:* central `WRITEBACK_DENYLIST` + authoritative-source resolver used by
  all reverse writers.
- *Tests:* same-window conflict → BMO value wins on execution fields;
  Sprint/Story Points/Epic never write to BMO.
- *Acceptance:* Jira-only field edit never touches BMO. · *Depends:* D9, D10.

**D12 — E2E harness + pilot enablement.**
- *Files:* new `tests/e2e/` driving Scenarios 1–4 against a Jira sandbox + BMO
  test component; flip pilot flag for Core :: Machine Learning: On-Device.
- *Tests:* Scenarios 1–4 within the 5-min SLA; **Invariant A** regression
  (re-sync of already-linked bug → zero new issues).
- *Acceptance:* all four PRD §8 scenarios green. · *Depends:* D9–D11.

**Dependency graph:** D1 → {D2, D3, D4→D5, D6, D8}; D7 standalone;
{D6, D7, D8} → D9 & D10 → D11 → D12.

---

## 10. Phase 2 & 3 (outline)

**Phase 2 — hierarchy, release data, identity, visibility**
- P2-1 metabug→epic (R-05); P2-2 bug-under-metabug→epic task (R-06);
  P2-3 re-parent protection + link preservation (R-07/R-08);
  P2-4 release flags + target milestone model & mapping (R-09/R-10, incl.
  per-component field-availability confirmation with BMO admins);
  P2-5 identity map hardening (R-11); P2-6 visibility enforcement (R-12).

**Phase 3 — accuracy & cleanup**
- P3-1 reconciliation report (R-13) + unresolved-identity surfacing;
  P3-2 spike: auto delivery-epic attachment for newly-filed bugs (PRD §4.2).

---

## 11. Testing strategy

- **Every PR includes:** unit tests for new lines (repo has `pytest-cov`, 75%
  floor via `make test`), a green full-suite run, and `make lint` clean (ruff +
  mypy + bandit + detect-secrets via pre-commit).
- **Reuse the harness:** `factory-boy` factories (add `JiraWebhookEventFactory`;
  extend `BugFactory` with a `with_release_flags` trait for Phase 2), autouse
  `mocked_jira` / `mocked_bugzilla`, `responses` for HTTP-level, `TestClient` via
  `authenticated_client`, `tmp_path` `dl_queue`, and the `no_mocked_*` markers for
  contract tests.
- **New test modules mirror source:** `tests/unit/jira_inbound/`,
  `tests/unit/test_jira_steps.py`, `tests/unit/test_identity.py`,
  `tests/unit/test_visibility.py`, and a new `tests/e2e/`.
- **Invariant regression tests are permanent:** A (no duplicate Jira issue on
  re-sync) and B (no BMO bug from inbound) live in the suite so no future change
  can silently break them.
- **Reviewability:** each PR is additive and flag-off, so it merges without
  changing production behavior; rollback = flip the flag. Reverse writers stay
  disabled until D6's loop/correlation gate and D7's idempotency are both merged.

---

## 12. Risks / to-verify

1. **Jira email visibility** on the JBI-target instance (confirmed for
   mozilla-hub; verify same instance).
2. **Jira Automation egress** allowed from each project (pilot first).
3. **Terminal-state handling** for Phabricator transitions (don't move closed issues).
4. **Release-flag field shape** in BMO for the pilot (exact `cf_status_firefox*`).
5. **Multi-instance:** file-based DLQ (`queue.py`) assumes ~single instance —
   pre-existing; revisit only if we scale out.
6. **Comment attribution format** for BMO write-back — confirm wording with
   stakeholders.

---

## 13. Open questions (from PRD §9)

- Pilot scope confirmed: Core :: Machine Learning: On-Device.
- Conflict resolution policy confirmed: BMO wins for execution fields.
- Story Points / Iteration availability per synced Component — confirm with BMO
  admins during Phase 2 field-mapping work.
- Relationship to existing JBI automation — extend, do not replace (constraint #6).
