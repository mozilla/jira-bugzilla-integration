![build badge](https://github.com/mozilla/jira-bugzilla-integration/actions/workflows/build-image.yaml/badge.svg)
![test badge](https://github.com/mozilla/jira-bugzilla-integration/actions/workflows/test-build.yaml/badge.svg)

# Jira Bugzilla Integration (JBI)
System to sync Bugzilla bugs to Jira issues.

### Caveats
- The system accepts webhook events from Bugzilla
- The events are transformed into Jira issues
- The system POSTs an update to the see_also field of Bugzilla bugs
- Bugs' whiteboard tags are used to determine if they should be synchronized or ignored

## Action Configuration
The system reads the action configuration from a YAML file, one per environment. Each entry controls the synchronization between Bugzilla tickets with Jira issues.


Below is a full example of an action configuration:
```yaml
    action: src.jbi.whiteboard_actions.default
    contact: [example@allizom.com]
    description: example configuration
    enabled: true
    parameters:
      jira_project_key: EXMPL
      whiteboard_tag: example
```

A bit more about the different fields...
- `action` (optional)
    - string
    - default: [src.jbi.whiteboard_actions.default](src/jbi/whiteboard_actions/default.py)
    - The specified Python module must be available in the `PYTHONPATH`
- `contact`
    - list of strings
    - If an issue arises with the workflow, communication will be established with these contacts
    - Please enter the contact information for one or more stakeholders
- `description`
    - string
    - Please enter a description; for example, team name or project use-case.
- `enabled` (optional)
    - bool [true, false]
    - default: false
    - If false, matching events will not be synchronized
- `parameters` (optional)
    - dict
    - default: {}
    - The parameters will be validated to ensure the selected action accepts the specified values
    - The [default action](src/jbi/whiteboard_actions/default.py) expects both the `whiteboard_tag` and `jira_project_key` fields



[View 'dev'  configurations here.](config/config.dev.yaml)

[View 'prod' configurations here.](config/config.prod.yaml)


### Custom Actions
If you're looking for a unique capability for your team's data flow, you can add your own python methods and functionality[...read more here.](src/jbi/whiteboard_actions/README.md)
