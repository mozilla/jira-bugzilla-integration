# Per-project mappings

A team can set its own Bugzilla ↔ Jira mappings by adding a file here named
after its Jira project key, for example `AIACT.yaml`. Open a pull request to
add or change it.

JBI applies the file to every action whose `jira_project_key` matches, in
every environment. **Values here win** over the action's values in
`config/config.<env>.yaml`, which in turn win over JBI's built-in defaults.

```yaml
jira_project_key: AIACT          # must match the file name

# Bugzilla status/resolution -> Jira status (Bugzilla -> Jira)
status_map:
  ASSIGNED: In Progress
  REOPENED: In Progress
  FIXED: Done
  WONTFIX: Done

# Bugzilla resolution -> Jira resolution. Also read in reverse (Jira -> Bugzilla),
# so two Bugzilla resolutions cannot map to the same Jira resolution.
resolution_map:
  FIXED: Done
  WONTFIX: "Won't Do"
  DUPLICATE: Duplicate

# Phabricator review moves (Bugzilla -> Jira)
phabricator_review_status: Code Review
phabricator_changes_requested_status: In Progress

# The label that pauses sync for one issue; `null` turns it off for the project
sync_stop_label: jbi-sync-stop

# Phase 2 features, on by default for onboarded teams
metabug_epics_enabled: false     # no Epics for metabugs, no parent seeding
release_data_enabled: false      # no bz-status-/bz-milestone- labels
```

Other keys you can set: `priority_map`, `severity_map`, `issue_type_map`,
`cf_fx_points_map`, `reverse_status_overrides`, `default_reverse_resolution`,
`jira_priority_field`, `jira_resolution_field`, `jira_severity_field`,
`jira_cf_fx_points_field`. See `docs/actions.md` for what each one does.

Notes:

- A map **replaces** the action's map completely; it is not merged entry by
  entry. List every value you need.
- Unknown keys are rejected, so a typo fails CI instead of being ignored.
- Onboarding (`jira_inbound_enabled`), steps and comment copying are not set
  here; they stay in the main config.
