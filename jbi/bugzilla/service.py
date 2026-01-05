import logging
from functools import lru_cache

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
