# Onboarding a team to two-way BMO ↔ Jira sync

JBI has synced Bugzilla bugs into Jira for years. Two-way sync adds the other
direction: changes made in Jira are written back to the linked bug. A team is
onboarded entirely through configuration in this repo. Nobody configures
anything in their own Jira project.

## 1. Onboard (one PR)

In `config/config.<env>.yaml`, the team's action needs `jira_inbound_enabled`:

```yaml
- whiteboard_tag: myteam          # bugs with [myteam] in the whiteboard sync
  bugzilla_user_id: 123456
  description: My team
  parameters:
    jira_project_key: MYTEAM
    jira_inbound_enabled: true    # <- two-way sync, and everything below
```

The JBI operators then add the project to the **Jira webhook's JQL filter**
(`project in (..., MYTEAM)`). The webhook sends Jira events to JBI, which
queues them on Pub/Sub for the consumer that writes to Bugzilla. Mirror the
same JQL in `JIRA_INBOUND_JQL` wherever the reconciliation report runs, so the
report flags the two if they drift apart. Both steps are needed: a project in
the webhook but not onboarded in config is ignored, and an onboarded project
missing from the webhook never sends JBI anything.

The Jira Automation bot needs `CREATE_ISSUES`, `EDIT_ISSUES`, `ADD_COMMENTS`,
`DELETE_ISSUES`, `TRANSITION_ISSUES` and `LINK_ISSUES` in the project, plus
the global "Browse users and groups" permission. Forward sync needs these
already.

## 2. What the team gets by default

| Area | Behavior |
|---|---|
| Jira → Bugzilla | Status (by Jira status category), resolution, priority, assignee and summary are written back. Comments are copied as "from Jira, by \<name\>: …". **Bugzilla wins** a conflict on these fields. |
| Never copied | Nothing reaches a private or security-restricted bug. Nothing comes from a restricted Jira comment or an issue with a security level. Planning fields (sprint, points, epic/parent, rank, fix version, labels, components) are never written to Bugzilla. |
| Patch review | Patch posted or `review?` → **In Review**. `review-` → **In Progress**. `review+` → no move. |
| Pause | The Jira label **`jbi-sync-stop`** pauses both directions for that issue. Remove it to resume. |
| Metabugs | A bug with `meta` in its Keywords and the team's tag becomes an **Epic**. A bug related to it (its Blocks, or the metabug's Depends On) becomes a child of that Epic if it has no parent yet, and keeps an existing parent otherwise, with a "blocks" link instead. Nested metabugs show up as "blocks" links between Epics. |
| Release data | `status-firefoxNN` flags and Target Milestone appear as labels, e.g. `bz-status-157-fixed` and `bz-milestone-157-branch`. |

## 3. Adjust or opt out (optional, a second PR)

A team can add `config/projects/<JIRA_KEY>.yaml` with its own mappings, and
those win over the defaults. See `config/projects/README.md` for every key.
Common ones:

```yaml
jira_project_key: MYTEAM
phabricator_review_status: Code Review       # if "In Review" isn't in the workflow
status_map: { ASSIGNED: Doing, FIXED: Done } # replaces the map entirely
metabug_epics_enabled: false                 # switch Phase 2 features off
release_data_enabled: false
reverse_comment_sync_enabled: false          # stop copying Jira comments (off only)
```

Unknown keys fail CI. Onboarding, steps and turning comment copying *on* stay
in the main config, because they decide whether JBI touches a team's data at
all.

Existing metabugs get Epics as they change. To create them all now (it does
a dry run unless you pass `--apply`):

```sh
PYTHONPATH=. uv run python bin/ensure_metabug_epics.py --tag myteam
```

## 4. Keeping it healthy

`python -m jbi reconcile` is a read-only report of where Bugzilla and Jira
disagree for every onboarded team:
- drifted fields;
- issues closed in Jira that Bugzilla couldn't be given a resolution for;
- people JBI can't match (fix these with an entry in
  `config/identity_map.<env>.yaml`);
- tagged bugs that never synced;
- a Jira webhook filter that doesn't match the config.

Run it on a schedule. Each finding is a log line plus a
`jbi.reconcile.findings.<kind>` gauge.

```sh
python -m jbi reconcile --days 30 --format md --output report.md
```

## 5. Testing

- `make smoke` runs the live end-to-end suite against a sandbox. See
  `tests/e2e/README.md`.
- `.local-harness/` (git-ignored) has the tools for driving a local JBI by
  hand.
