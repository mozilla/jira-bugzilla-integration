# Action
The system reads the actions configuration from a YAML file, one per environment. Each entry controls the synchronization between Bugzilla tickets and the Jira issues.

## Configuration

Below is a full example of an action configuration:
```yaml
- whiteboard_tag: example
  allow_private: false
  bugzilla_user_id: 514230
  description: example configuration
  module: jbi.actions.default
  parameters:
    jira_project_key: EXMPL
```

A bit more about the different fields...
- `whiteboard_tag`
    - string
    - The tag to be matched in the Bugzilla `whiteboard` field
- `allow_private` (optional)
    - bool [true, false]
    - default: false
    - If `false`, bugs will not be synchronized if they are not public. Note that in order to synchronize private bugs,
     the bugzilla user that JBI runs as must be in the security groups that are making the bug private.
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
- `module` (optional)
    - string
    - default: [jbi.actions.default](jbi/actions/default.py)
    - The specified Python module must be available in the `PYTHONPATH`
- `parameters` (optional)
    - dict
    - default: {}
    - The parameters will be validated to ensure the selected action accepts the specified values


[View 'nonprod'  configurations here.](../config/config.nonprod.yaml)

[View 'prod' configurations here.](../config/config.prod.yaml)


## Available Actions

### Default
The `jbi.actions.default` action will take the list of steps to be executed when
the Webhook is received from configuration.
When none is specified, it will create or update the Jira issue, publish comments when
assignee, status, or resolution are changed, or when a comment is posted on the Bugzilla ticket.

It will also set the Jira issue URL in the Bugzilla bug `see_also` field, and add a link
to the Bugzilla ticket on the Jira issue.

**Parameters**

- `jira_project_key` (**mandatory**)
    - string
    - The Jira project identifier
- `steps` (optional)
    - mapping [str, list[str]]
    - If defined, the specified steps are executed. The group of steps listed under `new` are executed when a Bugzilla event occurs on a ticket that is unknown to Jira. The steps under `existing`, when the Bugzilla ticket is already linked to a Jira issue. The steps under `comment` when a comment is posted on a linked Bugzilla ticket.
    If one of these groups is not specified, the default steps will be used.
- `jira_components` (optional)
   - list [str]
   - If defined, the created issues will be assigned the specified components.
- `sync_whiteboard_labels` (optional)
    - boolean
    - Whether to sync the Bugzilla status whiteboard labels to Jira. Defaults to `true`.
- `status_map` (optional)
    - mapping [str, str]
    - If defined, map the Bugzilla bug status (or resolution) to Jira issue status
- `resolution_map` (optional)
    - mapping [str, str]
    - If defined, map the Bugzilla bug resolution to Jira issue resolution

Minimal configuration:
```yaml
    whiteboard_tag: example
    bugzilla_user_id: 514230
    description: minimal configuration
    parameters:
      jira_project_key: EXMPL
```

Full configuration, that will set assignee, change the Jira issue status and resolution.

```yaml
- whiteboard_tag: fidefe
  bugzilla_user_id: 514230
  description: full configuration
  module: jbi.actions.default
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
  then the assignee is cleared from the Jira issue. The Jira account that JBI uses requires the "Browse
  users and groups" global permission in order to set the assignee.
- `maybe_update_issue_resolution`:
  If the Bugzilla ticket resolution field is specified in the `resolution_map` parameter, it will set the
  Jira issue resolution.
- `maybe_update_issue_status`:
  If the Bugzilla ticket status field is specified in the `status_map` parameter, it will set the
  Jira issue status.
- `update_issue`
- `add_jira_comments_for_changes`
- `maybe_assign_jira_user`
- `maybe_update_issue_resolution`
- `maybe_update_issue_status`
- `create_comment`

### Custom Actions

If you're looking for a unique capability for your team's data flow, you can add your own Python methods and functionality[...read more here.](../jbi/actions/README.md)
