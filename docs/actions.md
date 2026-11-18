# Action
The system reads the action configurations from a YAML file, one per environment. Each entry controls the synchronization between Bugzilla tickets and Jira issues.

## Configuration

Below is an example of an action configuration:
```yaml
- whiteboard_tag: example
  bugzilla_user_id: 514230
  description: example configuration
  parameters:
    jira_project_key: EXMPL
```

A bit more about the different fields...
- `whiteboard_tag`
    - string
    - The tag to be matched in the Bugzilla `whiteboard` field
- `bugzilla_user_id`
    - a bugzilla user id, a list of user ids, or a literal "tbd" to signify that no bugzilla user id is available
    - If an issue arises with the workflow, communication will be established with these users
    - Please enter the user information for one or more stakeholders
- `description`
    - string
    - Please enter a description; for example, team name or project use-case.
- `enabled` (optional)
    - bool [true, false]
    - default: true
    - If false, matching events will not be synchronized
- `parameters` (optional)
    - `ActionParams`
    - The parameters passed to step functions when the action is run (see below)


[View 'nonprod'  configurations here.](../config/config.nonprod.yaml)

[View 'prod' configurations here.](../config/config.prod.yaml)


### Parameters

Parameters are used by `step` functions to control what Bugzilla data is synced with Jira issues. Possible parameters are:

- `jira_project_key` (**mandatory**)
    - string
    - The Jira project identifier
- `steps` (optional)
    - mapping [str, list[str]]
    - If defined, the specified steps are executed. The group of steps listed under `new` are executed when a Bugzilla event occurs on a ticket that is unknown to Jira. The steps under `existing`, when the Bugzilla ticket is already linked to a Jira issue. The steps under `comment` when a comment is posted on a linked Bugzilla ticket.
    If one of these groups is not specified, the default steps will be used.
    - **Note:** When the whiteboard tag is added to a bug that already has a linked Jira issue (for example, when a tag is removed and later re-added, or when a `see_also` link to an existing Jira issue is added at the same time as the tag), the `new` steps are run instead of the `existing` steps. This performs a full resync of all current bug fields to the Jira issue, rather than only reacting to the fields that changed in that single event.
- `jira_components` (optional)
   - object
   - Controls how Jira components are set on issues in the `maybe_update_components` step.
     - `use_bug_component` (optional)
        - bool
        - Set bug's component as issue component, eg. ``General`` (default `true`)
     - `use_bug_product` (optional)
        - bool
        - Set bug's product as issue component, eg. ``Core`` (default `false`)
     - `use_bug_component_with_product_prefix` (optional)
       - bool
       - Set bug's full component as issue component, eg. ``Core::General`` (default `false`)
     - `set_custom_components` (optional)
        - list[str]
        - If defined, the issues will be assigned the specified components (default `[]`)
     - create_components  (optional)
        - bool
        - If true, components that do not exist in Jira will be created (default `false`)
        - Set `use_bug_component` to `false` when this open is `true`.
- `labels_brackets` (optional)
    - enum ["yes", "no", "both"]
    - Controls whether issue labels should have brackets or not in the `sync_whiteboard_labels` step (default: "no")
- `status_map` (optional)
    - mapping [str, str]
    - If defined, map the Bugzilla bug status (or resolution) to Jira issue status
- `resolution_map` (optional)
    - mapping [str, str]
    - If defined, map the Bugzilla bug resolution to Jira issue resolution
- `issue_type_map` (optional)
    - mapping [str, str]
    - If defined, map the Bugzilla type to Jira issue type (default: ``Bug`` if ``defect`` else ``Task``)
- `linked_project_excludes` (optional)
    - list[str]
    - Project keys to exclude from Jira issue link syncing (default: `["BZFFX"]`).

Minimal configuration:
```yaml
    whiteboard_tag: example
    bugzilla_user_id: 514230
    description: minimal configuration
    parameters:
      jira_project_key: EXMPL
```

A configuration that will set an assignee and change the Jira issue status and resolution.

```yaml
- whiteboard_tag: fidefe
  bugzilla_user_id: 514230
  description: full configuration
  parameters:
    jira_project_key: FIDEFE
    steps:
      new:
      - create_issue
      - maybe_delete_duplicate
      - add_link_to_bugzilla
      - add_link_to_jira
      - maybe_assign_jira_user
      - maybe_update_issue_resolution
      - maybe_update_issue_status
      existing:
      - update_issue
      - add_jira_comments_for_changes
      - maybe_assign_jira_user
      - maybe_update_issue_resolution
      - maybe_update_issue_status
      comment:
      - create_comment
    status_map:
      ASSIGNED: In Progress
      FIXED: Closed
      WONTFIX: Closed
      DUPLICATE: Closed
      INVALID: Closed
      INCOMPLETE: Closed
      WORKSFORME: Closed
      REOPENED: In Progress
    resolution_map:
      FIXED: Done
      DUPLICATE: Duplicate
      WONTFIX: "Won't Do"
```

In this case if the bug changes to the NEW status the action will attempt to set the linked Jira
issue status to "In Progress". If the bug changes to RESOLVED FIXED it will attempt to set the
linked Jira issue status to "Closed". If the bug changes to a status not listed in `status_map` then no change will be made to the Jira issue.

### Defaults for bidirectional sync

An action with `jira_inbound_enabled: true` gets these settings without having to configure them:

| Parameter | Default | Effect |
|---|---|---|
| `phabricator_review_status` | `In Review` | A Phabricator patch attached to the bug moves the Jira issue to this status. |
| `phabricator_changes_requested_status` | `In Progress` | A reviewer setting `review-` moves the issue to this status. |
| `sync_stop_label` | `jbi-sync-stop` | This label on the Jira issue pauses sync in both directions; removing it resumes. |
| `reverse_comment_sync_enabled` | `true` | Jira comments are copied to the bug as "from Jira, by \<name\>: …". Never onto a private or security-restricted bug, and never a Jira comment with restricted visibility or on an issue with a security level. |
| `metabug_epics_enabled` | `true` | Metabugs become Epics and new issues are parented under them (see below). Adds `sync_dependencies` and `maybe_seed_epic_parent`. |
| `release_data_enabled` | `true` | Release flags and Target Milestone are mirrored as labels (see below). Adds `mirror_release_flags` and `mirror_target_milestone`. |

The two review steps (`maybe_update_issue_status_on_patch`, `sync_phabricator_review_state`) are
added to the default `attachment` steps. The Phase 2 steps are appended to the action's `new` and
`existing` steps, including custom lists, unless already listed. Issues already in a done status are never moved, and if the
project's workflow has no transition to the status, JBI logs a warning and skips it.

To customize, set the parameter on the action or in the project file. To turn one off, set it to
`null` (`false` for `reverse_comment_sync_enabled` and the two Phase 2 switches). An action that
lists its own `attachment` steps keeps exactly that list. Actions without `jira_inbound_enabled`
are unaffected.

### Per-project mappings

A team can keep its own mappings in `config/projects/<JIRA_KEY>.yaml`, applied to every action
with that `jira_project_key` in every environment. Values there win over the action's values. See
[`config/projects/README.md`](../config/projects/README.md) for the allowed keys.

### Metabugs and release data (Phase 2)

- `metabug_epics_enabled` (on for onboarded actions): with the `maybe_seed_epic_parent` step in `new`, a newly
  created issue is parented under an Epic mirroring the lowest-numbered metabug it blocks (a bug with
  the `meta` keyword). The Epic is created the first time a child needs it. The parent is set once,
  at creation; JBI never re-parents an issue afterwards, and a parent change in Jira is never written
  to Bugzilla. A metabug already mirrored as a non-Epic issue is skipped, and private or
  security-restricted metabugs are never mirrored. Other metabugs stay visible as issue links through
  `sync_dependencies`.
- `mirror_release_flags` and `mirror_target_milestone` steps: mirror `cf_status_firefoxNN` flags
  and Target Milestone onto the issue as labels, e.g. `bz-status-157-fixed` and
  `bz-milestone-157-branch`. JBI owns the `bz-status-` and `bz-milestone-` label prefixes and removes
  labels there that Bugzilla no longer backs. No other label is touched.

### Available Steps

- `create_issue`
- `maybe_delete_duplicate`
- `add_link_to_bugzilla`
- `add_link_to_jira`
- `maybe_assign_jira_user`:
  It will attempt to assign the Jira issue the same person as the bug is assigned to. This relies on
  the user using the same email address in both Bugzilla and Jira. If the user does not exist in Jira
  then the assignee is cleared from the Jira issue.
  **Requirements**: The Jira account that JBI uses requires the "Browse users and groups" global permission in order to set the assignee.
- `maybe_update_issue_resolution`:
  If the Bugzilla ticket resolution field is specified in the `resolution_map` parameter, it will set the
  Jira issue resolution.
  **Requirements**: ``resolution`` field must be present on issue forms (or configure `jira_resolution_field`).
- `maybe_update_issue_status`:
  If the Bugzilla ticket status field is specified in the `status_map` parameter, it will set the
  Jira issue status.
- `add_jira_comments_for_changes`
- `maybe_update_issue_priority`
  **Requirements**: ``priority`` field must be present on issue forms (or configure `jira_priority_field`).
- `maybe_update_issue_resolution`
- `maybe_update_issue_severity`
  **Requirements**: ``customfield_10319`` field must be present on issue forms (or configure `jira_severity_field`).
- `maybe_update_issue_status`
- `maybe_update_issue_points`
   **Requirements**: ``customfield_10037`` field must be present on issue forms (or configure `jira_cf_fx_points_field`).
- `maybe_update_issue_type`:
  Updates the Jira issue type when the Bugzilla bug `type` field changes, using the `issue_type_map` parameter (the same mapping `create_issue` uses on creation). Unmapped types fall back to `Task`. Runs on UPDATE events only — the issue type is set at creation time by `create_issue`; add this step to the `existing` steps to keep the type in sync afterwards.
  **Requirements**: The target Jira project must permit changing an issue's type (Jira may reject some transitions, e.g. to/from sub-tasks or across incompatible screen schemes).
- `create_comment`
- `sync_keywords_labels`
- `sync_whiteboard_labels`:
  Syncs the Bugzilla whitboard tags field to the Jira labels field.
- `maybe_update_components`: looks at the component that's set on the bug (if any) and any components added to the project configuration with the `jira_components` parameter (see above). If those components are available on the Jira side as well, they're added to the Jira issue
- `maybe_add_phabricator_link`: looks at an attachment and if it is a phabricator attachment, it gets added as a link or updated if the attachment was previously added.
- `maybe_update_issue_status_on_patch`: when a Phabricator patch is attached, moves the issue to `phabricator_review_status`. Does nothing if that is unset.
- `maybe_seed_epic_parent`: see "Metabugs and release data" above.
- `mirror_release_flags`, `mirror_target_milestone`: see "Metabugs and release data" above.
- `sync_phabricator_review_state`: when a reviewer sets `review-` on a patch, moves the issue to `phabricator_changes_requested_status`. Does nothing if that is unset.
- `sync_dependencies`:
  Creates and removes Jira "Blocks" issue links based on Bugzilla's `depends_on` and `blocks` fields. For `depends_on`: if bug A depends on bug B and bug B has a linked Jira issue, a link is created where B's issue blocks A's issue. For `blocks`: if bug A blocks bug B and bug B has a linked Jira issue, a link is created where A's issue blocks B's issue. Supports cross-project linking when related bugs have multiple Jira issues. Silently skips bugs that are private, missing, or have no Jira issues. On UPDATE operations, only processes fields that actually changed.
- `sync_see_also`:
  Creates and removes Jira "Relates" issue links when Bugzilla's `see_also` field changes. Handles two types of URLs: Jira issue URLs are linked directly; Bugzilla bug URLs cause the linked bug's Jira issue(s) to be looked up and linked. Key order is normalised so that links created from both sides of a Bugzilla `see_also` relationship (which Bugzilla mirrors bidirectionally) produce the same Jira link, preventing duplicates. Silently skips URLs that are private, missing, or resolve to no Jira issues. On UPDATE operations, only processes when the `see_also` field changes.
- `sync_duplicates`:
  Creates and removes Jira "Duplicate" issue links based on Bugzilla's `dupe_of` and `duplicates` fields. When `dupe_of` is set (this bug is a duplicate of another), a link is created where this issue is the inward (duplicate) side. When `duplicates` changes (another bug is marked as a duplicate of this one), a link is created where the other issue is the inward side. Silently skips bugs that are private, missing, or have no Jira issues. Only runs on UPDATE events, since a bug cannot be filed as a duplicate — `dupe_of` is set when resolving an existing bug as DUPLICATE.
- `sync_regressions`:
  Creates and removes Jira "Problem/Incident" issue links based on Bugzilla's `regressions` and `regressed_by` fields. When `regressions` is set (this bug caused another to regress), a link is created where this issue is the inward (causing) side. When `regressed_by` is set (this bug was caused to regress by another), a link is created where the other issue is the inward side. Silently skips bugs that are private, missing, or have no Jira issues. Runs on both CREATE and UPDATE events, since regression links can be set when filing a new bug.
