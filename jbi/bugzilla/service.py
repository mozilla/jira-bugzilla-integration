import logging
from functools import lru_cache

import requests
from statsd.defaults.env import statsd

from jbi import Operation, environment
from jbi.common.instrument import ServiceHealth
from jbi.models import (
    ActionContext,
    BugzillaBug,
)

from .client import BugzillaClient, BugzillaClientError

settings = environment.get_settings()

logger = logging.getLogger(__name__)


class BugzillaService:
    """Used by action workflows to perform action-specific Bugzilla tasks"""

    def __init__(self, client: BugzillaClient) -> None:
        self.client = client

    def check_health(self) -> ServiceHealth:
        """Check health for Bugzilla Service"""
        logged_in = self.client.logged_in()
        all_webhooks_enabled = False
        if logged_in:
            all_webhooks_enabled = self._all_webhooks_enabled()

        health: ServiceHealth = {
            "up": logged_in,
            "all_webhooks_enabled": all_webhooks_enabled,
        }
        return health

    def _all_webhooks_enabled(self):
        # Check that all JBI webhooks are enabled in Bugzilla,
        # and report disabled ones.

        try:
            jbi_webhooks = self.client.list_webhooks()
        except (BugzillaClientError, requests.HTTPError):
            return False

        if len(jbi_webhooks) == 0:
            logger.info("No webhooks enabled")
            return True

        for webhook in jbi_webhooks:
            # Report errors in each webhook
            statsd.gauge(f"jbi.bugzilla.webhooks.{webhook.slug}.errors", webhook.errors)
            # Warn developers when there are errors
            if webhook.errors > 0:
                logger.warning(
                    "Webhook %s has %s error(s)", webhook.name, webhook.errors
                )
            if not webhook.enabled:
                logger.error(
                    "Webhook %s is disabled (%s errors)",
                    webhook.name,
                    webhook.errors,
                )
                return False
        return True

    def add_link_to_jira(self, context: ActionContext):
        """Add link to Jira in Bugzilla ticket"""
        bug = context.bug
        issue_key = context.jira.issue
        jira_url = f"{settings.jira_base_url}browse/{issue_key}"
        logger.debug(
            "Link %r on Bug %s",
            jira_url,
            bug.id,
            extra=context.update(operation=Operation.LINK).model_dump(),
        )
        return self.client.update_bug(bug.id, see_also={"add": [jira_url]})

    def get_description(self, bug_id: int):
        """Fetch a bug's description

        A Bug's description does not appear in the payload of a bug. Instead, it is "comment 0"
        """

        comment_list = self.client.get_comments(bug_id)
        comment_body = comment_list[0].text if comment_list else ""
        return str(comment_body)

    def refresh_bug_data(self, bug: BugzillaBug):
        """Re-fetch a bug to ensure we have the most up-to-date data"""

        updated_bug = self.client.get_bug(bug.id)
        # When bugs come in as webhook payloads, they have a "comment"
        # attribute, but this field isn't available when we get a bug by ID.
        # So, we make sure to add the comment back if it was present on the bug.
        updated_bug.comment = bug.comment
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
