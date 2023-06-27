# Restrict Workflow Customization and Validate Default Action Parameters

- Status: Accepted
- Date: 2023-06-23

Tracking issue: [#544](https://github.com/mozilla/jira-bugzilla-integration/issues/544)

## Context and Problem Statement

When JBI was first designed, we envisioned a scenario where a user might want to contribute a Python module to create an entirely custom sync workflow. As we've continued to make workflow customization easier, we've questioned whether this freedom of customization is worth the added complexity of supporting custom modules and action parameters.

## Decision Drivers

- Reduce complexity in handling custom modules
- Prevent bugs due to misconfigured workflows
- Align with the evolved designs that emphasize customization through combining steps

## Considered Options

- Option 1: Maintain the ability to customize workflows through custom modules and parameters
- Option 2: Restrict customization to the default action and validate action parameters with a schema

## Decision Outcome

Considering the positive consequences of Option 2 and the fact that workflow customization is still possible through action steps, it is reasonable to choose Option 2 to simplify workflow customization and focus on improving the reliability and robustness of a single action.

## Pros and Cons of the Options

### Option 1: Maintain the ability to customize workflows through custom modules and parameters

- Good, because it provides flexibility for users to create entirely custom action workflows
- Bad, because it increases complexity in handling different parameter structures and custom module configurations

### Option 2: Restrict customization to the default action and enforce parameters with a schema

- Good, because it validates that configured parameters are useable by step functions
- Good, because we can safely assume that action parameters are of a certain type
- Good, because it aligns with the evolved designs that emphasize customization through action steps
- Bad, because it limits customization ability compared to the previous method with custom modules
