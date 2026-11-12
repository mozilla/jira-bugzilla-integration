"""Publish-time gates for inbound Jira events.

The web process runs these before putting an event on Pub/Sub; the consumer
does the real work later (correlation, Bugzilla fetch, action matching). They
exist so that traffic JBI can never act on -- the overwhelming majority of what
Jira Automation forwards -- is dropped cheaply instead of being queued.

Two rules shape everything here:

* **Payload and configuration only.** No Jira or Bugzilla call, so publishing
  stays fast and keeps working when either system is down.
* **When unsure, publish.** Dropping an event here is unrecoverable, while a
  published event the consumer ignores costs one message. The publisher does not
  know which action a bug will match (that needs the bug's whiteboard), so a
  per-action rule only skips an event when *every* action that could handle it
  agrees.
"""

from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Optional

from jbi.jira_inbound.handler import is_bot_authored
from jbi.jira_inbound.models import JiraWebhookRequest
from jbi.models import Action, Actions
from jbi.writeback import sync_is_stopped


class SkipReason(StrEnum):
    """Why an event was not published. Each value is a metric name suffix."""

    NO_ISSUE_KEY = auto()
    NO_PROJECT = auto()
    BOT_AUTHORED = auto()
    PROJECT_NOT_ENABLED = auto()
    STOP_LABEL = auto()
    NO_CHANGELOG = auto()


@dataclass(frozen=True)
class Skip:
    """A decision not to publish, with a human-readable explanation."""

    reason: SkipReason
    detail: str


def _inbound_actions(project_key: str, actions: Actions) -> list[Action]:
    """The actions that could handle an event from this Jira project."""
    return [
        action
        for action in actions
        if action.jira_project_key == project_key
        and action.parameters.jira_inbound_enabled
    ]


def preflight(event: JiraWebhookRequest, actions: Actions) -> Optional[Skip]:
    """Return why this event should not be published, or `None` to publish it."""
    issue = event.issue
    if issue is None or not issue.key:
        return Skip(SkipReason.NO_ISSUE_KEY, "no issue key in payload")
    issue_key = issue.key

    project_key = issue.project_key
    if not project_key:
        return Skip(
            SkipReason.NO_PROJECT, f"cannot determine project of issue {issue_key}"
        )

    # Echo suppression (Invariant C): JBI's own writes must not come back.
    if is_bot_authored(event):
        return Skip(
            SkipReason.BOT_AUTHORED, f"event on {issue_key} authored by JBI itself"
        )

    candidates = _inbound_actions(project_key, actions)
    if not candidates:
        return Skip(
            SkipReason.PROJECT_NOT_ENABLED,
            f"no action with `jira_inbound_enabled` for project {project_key}",
        )

    # An absent `labels` key means "not sent", not "no labels": the consumer
    # fetches them, so there is nothing to decide here.
    labels = issue.fields.labels if issue.fields else None
    if labels is not None and all(
        sync_is_stopped(labels, action.parameters.sync_stop_label)
        for action in candidates
    ):
        return Skip(SkipReason.STOP_LABEL, f"sync stopped by a label on {issue_key}")

    if event.lacks_needed_changelog and not any(
        action.parameters.reverse_sync_without_changelog for action in candidates
    ):
        return Skip(
            SkipReason.NO_CHANGELOG,
            f"payload for {issue_key} carries no changelog, so JBI cannot tell "
            "which field changed",
        )

    return None
