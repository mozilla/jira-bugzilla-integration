import logging
from functools import lru_cache
from typing import Optional

import requests
from dockerflow import checks
from statsd.defaults.env import statsd

from jbi import environment

from .client import BugzillaClient, BugzillaClientError
from .models import Bug

settings = environment.get_settings()

logger = logging.getLogger(__name__)


class BugzillaService:
    """Used by action workflows to perform action-specific Bugzilla tasks"""

    def __init__(self, client: BugzillaClient) -> None:
        self.client = client

    def add_link_to_see_also(self, bug: Bug, link: str):
        """Add link to Bugzilla ticket"""

        return self.client.update_bug(bug.id, see_also={"add": [link]})

    # --- Reverse (Jira -> BMO) writes ------------------------------------
    #
    # Every writer below is read-before-write: it compares the value it is
    # about to write against the bug's current value and issues no request
    # when they already match. That is the backstop layer of Invariant C --
    # an echoed value terminates silently after one round trip instead of
    # oscillating, even when the actor-based echo gates cannot fire.
    #
    # Callers must pass a freshly-fetched bug (the inbound handler fetches one
    # per event); these methods deliberately do not re-fetch, so a caller can
    # apply several writes against one read.

    def _update_bug_if_changed(self, bug: Bug, changes: dict):
        """Apply the given field changes, skipping any that are already set."""
        pending = {
            field: value
            for field, value in changes.items()
            if value is not None and getattr(bug, field, None) != value
        }
        if not pending:
            logger.info(
                "Bug %s already matches %s, no update sent",
                bug.id,
                ", ".join(sorted(changes)),
                extra={"bug": {"id": bug.id}},
            )
            return None

        logger.info(
            "Updating Bug %s fields %s",
            bug.id,
            ", ".join(sorted(pending)),
            extra={"bug": {"id": bug.id}},
        )
        return self.client.update_bug(bug.id, **pending)

    def set_status_resolution(
        self,
        bug: Bug,
        status: Optional[str],
        resolution: Optional[str] = None,
        assigned_to: Optional[str] = None,
    ):
        """Set the bug's status, and optionally its resolution and assignee.

        All three travel in one request because BMO validates them as a set:
        a resolution without the matching status transition is rejected, and
        `ASSIGNED` is rejected outright on a bug with no assignee
        ("You cannot set this bug's status to ASSIGNED because the bug is not
        assigned to a person"). Passing the assignee alongside satisfies that
        in a single write rather than leaving the bug half-updated.
        """
        return self._update_bug_if_changed(
            bug,
            {
                "status": status,
                "resolution": resolution,
                "assigned_to": assigned_to,
            },
        )

    def set_assignee(self, bug: Bug, email: Optional[str]):
        """Assign the bug, or unassign it with BMO's `nobody@mozilla.org`."""
        return self._update_bug_if_changed(bug, {"assigned_to": email})

    def set_priority(self, bug: Bug, priority: Optional[str]):
        """Set the bug's priority."""
        return self._update_bug_if_changed(bug, {"priority": priority})

    def set_summary(self, bug: Bug, summary: Optional[str]):
        """Set the bug's summary."""
        return self._update_bug_if_changed(bug, {"summary": summary})

    def add_comment(self, bug: Bug, text: str):
        """Post a comment on the bug, unless an identical one already exists.

        A comment has no field to compare against, so the read-before-write
        equivalent is a duplicate check against the bug's existing comments.
        Without it, a replayed or echoed event would append the same text
        again on every delivery.
        """
        if not text:
            return None

        existing = self.client.get_comments(bug.id)
        if any(comment.text == text for comment in existing):
            logger.info(
                "Bug %s already has this comment, not posting it again",
                bug.id,
                extra={"bug": {"id": bug.id}},
            )
            return None

        return self.client.update_bug(bug.id, comment={"body": text})

    def get_description(self, bug_id: int):
        """Fetch a bug's description

        A Bug's description does not appear in the payload of a bug. Instead, it is "comment 0"
        """

        comment_list = self.client.get_comments(bug_id)
        comment_body = comment_list[0].text if comment_list else ""
        return str(comment_body)

    def refresh_bug_data(self, bug: Bug):
        """Re-fetch a bug to ensure we have the most up-to-date data"""

        refreshed_bug_data = self.client.get_bug(bug.id)
        # When bugs come in as webhook payloads, they have "comment" and "attachment"
        # attributes, but these fields aren't available when we get a bug by ID.
        # So, we make sure to add them back if they were present on the bug.
        updated_bug = refreshed_bug_data.model_copy(
            update={
                "comment": bug.comment,
                "attachment": bug.attachment,
            }
        )
        return updated_bug

    def get_bugs_by_ids(self, bug_ids: list[int]) -> dict[int, Bug]:
        """Fetch multiple bugs by their IDs.

        Returns a dictionary mapping bug_id -> Bug object.
        Silently skips bugs that are private/inaccessible or don't exist.
        """
        from .client import BugNotAccessibleError

        bugs_by_id = {}
        for bug_id in bug_ids:
            try:
                bug_data = self.client.get_bug(bug_id)
                bugs_by_id[bug_id] = bug_data
            except BugNotAccessibleError:
                logger.info(
                    "Skipping bug %s (not accessible)",
                    bug_id,
                    extra={"bug": {"id": bug_id}},
                )
            except requests.HTTPError as e:
                logger.info(
                    "Skipping bug %s (HTTP error: %s)",
                    bug_id,
                    e,
                    extra={"bug": {"id": bug_id}},
                )

        return bugs_by_id

    def list_webhooks(self):
        """List the currently configured webhooks, including their status."""

        return self.client.list_webhooks()

    def check_bugzilla_connection(self):
        if not self.client.logged_in():
            return [checks.Error("Login fails or service down", id="bugzilla.login")]
        return []

    def check_bugzilla_webhooks(self):
        # Do not bother executing the rest of checks if connection fails.
        if messages := self.check_bugzilla_connection():
            return messages

        # Check that all JBI webhooks are enabled in Bugzilla,
        # and report disabled ones.
        try:
            jbi_webhooks = self.list_webhooks()
        except (BugzillaClientError, requests.HTTPError) as e:
            return [
                checks.Error(
                    f"Could not list webhooks ({e})", id="bugzilla.webhooks.fetch"
                )
            ]

        results = []

        if len(jbi_webhooks) == 0:
            results.append(
                checks.Warning("No webhooks enabled", id="bugzilla.webhooks.empty")
            )

        for webhook in jbi_webhooks:
            # Report errors in each webhook
            statsd.gauge(f"jbi.bugzilla.webhooks.{webhook.slug}.errors", webhook.errors)
            # Warn developers when there are errors
            if webhook.errors > 0:
                results.append(
                    checks.Warning(
                        f"Webhook {webhook.name} has {webhook.errors} error(s)",
                        id="bugzilla.webhooks.errors",
                    )
                )

            if not webhook.enabled:
                results.append(
                    checks.Error(
                        f"Webhook {webhook.name} is disabled ({webhook.errors} errors)",
                        id="bugzilla.webhooks.disabled",
                    )
                )

        return results


@lru_cache(maxsize=1)
def get_service():
    """Get bugzilla service"""
    client = BugzillaClient(
        settings.bugzilla_base_url, api_key=str(settings.bugzilla_api_key)
    )
    return BugzillaService(client=client)
