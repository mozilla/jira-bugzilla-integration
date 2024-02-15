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
        # When bugs come in as webhook payloads, they have a "comment"
        # attribute, but this field isn't available when we get a bug by ID.
        # So, we make sure to add the comment back if it was present on the bug.
        updated_bug = refreshed_bug_data.model_copy(update={"comment": bug.comment})
        return updated_bug

    def list_webhooks(self):
        """List the currently configured webhooks, including their status."""

        return self.client.list_webhooks()


@lru_cache(maxsize=1)
def get_service():
    """Get bugzilla service"""
    client = BugzillaClient(
        settings.bugzilla_base_url, api_key=str(settings.bugzilla_api_key)
    )
    return BugzillaService(client=client)


@checks.register(name="bugzilla.up")
def check_bugzilla_connection():
    service = get_service()
    if not service.client.logged_in():
        return [checks.Error("Login fails or service down", id="bugzilla.login")]
    return []


@checks.register(name="bugzilla.all_webhooks_enabled")
def check_bugzilla_webhooks():
    service = get_service()

    # Do not bother executing the rest of checks if connection fails.
    if messages := check_bugzilla_connection():
        return messages

    # Check that all JBI webhooks are enabled in Bugzilla,
    # and report disabled ones.
    try:
        jbi_webhooks = service.list_webhooks()
    except (BugzillaClientError, requests.HTTPError) as e:
        return [
            checks.Error(f"Could not list webhooks ({e})", id="bugzilla.webhooks.fetch")
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
