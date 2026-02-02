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
- `create_comment`
- `sync_keywords_labels`
- `sync_whiteboard_labels`:
  Syncs the Bugzilla whitboard tags field to the Jira labels field.
- `maybe_update_components`: looks at the component that's set on the bug (if any) and any components added to the project configuration with the `jira_components` parameter (see above). If those components are available on the Jira side as well, they're added to the Jira issue
- `maybe_add_phabricator_link`: looks at an attachment and if it is a phabricator attachment, it gets added as a link or updated if the attachment was previously added.
- `sync_blocks_links`:
  Creates Jira "Blocks" issue links based on Bugzilla's `blocks` field. If bug A blocks bug B, and bug B has a linked Jira issue, a link is created where A's issue blocks B's issue. Supports cross-project linking when blocked bugs have multiple Jira issues. Silently skips blocked bugs that are private, missing, or have no Jira issues. On UPDATE operations, only processes when the `blocks` field changes.
- `sync_depends_on_links`:
  Creates Jira "Blocks" issue links based on Bugzilla's `depends_on` field. If bug A depends on bug B, and bug B has a linked Jira issue, a link is created where B's issue blocks A's issue. Supports cross-project linking when dependency bugs have multiple Jira issues. Silently skips dependencies that are private, missing, or have no Jira issues. On UPDATE operations, only processes when the `depends_on` field changes.
