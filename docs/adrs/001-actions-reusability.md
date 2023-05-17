# Composability of Actions

- Status: accepted
- Date: 2022-10-05

Tracking issue: https://github.com/mozilla/jira-bugzilla-integration/pull/232

## Context and Problem Statement

The gap between the default action behavior and custom workflow is too hard.
How to improve composability of workflows? How can we make it easier for customers
to create their own workflows without us?

## Decision Drivers

- Amount of efforts
- Code readability
- Reusability
- Configuration
- Testability

## Considered Options

1. Add parameters to default action
2. Split default action into reusable steps

## Decision Outcome

Chosen option: "[option 2]", because the amount of efforts to refactor
the actions code is justified by the benefits in terms of readability,
testability, reusability. The resulting configuration can be verbose, but
will be explicit.

## Pros and Cons of the Options

### Option 1 - Add parameters to default action

With this approach, we introduce parameters to the default action class, in
order to enable or disable certain parts of its workflow.

```yaml
whiteboard_tag: example
module: jbi.actions.default
parameters:
    jira_project_key: EXMPL
    sync_whiteboard_labels: false
    update_issue_resolution: true
```

- **Amount of efforts**: Low. Almost no refactoring necessary.
- **Code readability**: Bad. Having several combinations of parameters will result in a lot of code branches. Plus, in order to implement custom workflows, contributors will inherit the default action, which will result in a lot of indirections.
- **Reusability**: Bad. Reusing some bits of the default action won't be trivial without inheriting classes.
- **Configuration**: Easy. Document all available parameters.
- **Testability**: Bad. The number of combinations for all parameters can be huge and hard to test.

### Option 2 - Split default action into reusable steps

With this approach, we split the default action class into tiny functions called "steps".
The configuration lists the steps to be executed by context, whether a comment is
posted, a bug is created, or updated.

```yaml
whiteboard_tag: example
module: jbi.actions.default
parameters:
  jira_project_key: EXMPL
  steps:
    new:
    - create_issue
    - add_link_to_bugzilla
    - add_link_to_jira
    existing:
    - update_issue
    - add_jira_comments_for_changes
    comment:
    - create_comment
```

- **Amount of efforts**: High. The whole action code and its tests has to be refactored.
- **Code readability**: Great. Each step has its own limited scope.
- **Reusability**: Great. Reusing steps is trivial.
- **Configuration**: Verbose. Each workflow will repeat all necessary steps. It could also be hard to differentiate
  workflows if the list of steps is too long.
- **Testability**: Great. Each step has a limited scope, and is follows the functional programming paradigm.
