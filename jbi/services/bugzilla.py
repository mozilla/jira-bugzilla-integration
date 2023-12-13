"""Contains a Bugzilla REST client and functions comprised of common operations
with that REST client
"""

import logging
from functools import lru_cache

import requests
from statsd.defaults.env import statsd

from jbi import Operation, environment
from jbi.common.instrument import ServiceHealth, instrument
from jbi.models import (
    ActionContext,
    BugzillaApiResponse,
    BugzillaBug,
    BugzillaComment,
    BugzillaComments,
    BugzillaWebhooksResponse,
)

settings = environment.get_settings()

logger = logging.getLogger(__name__)


class BugzillaClientError(Exception):
    """Errors raised by `BugzillaClient`."""


instrumented_method = instrument(
    prefix="bugzilla",
    exceptions=(
        BugzillaClientError,
        requests.RequestException,
    ),
)


class BugzillaClient:
    """A wrapper around `requests` to interact with a Bugzilla REST API."""

    def __init__(self, base_url, api_key):
        """Initialize the client, without network activity."""
        self.base_url = base_url
        self.api_key = api_key
        self._client = requests.Session()

    def _call(self, verb, url, *args, **kwargs):
        """Send HTTP requests with API key in querystring parameters."""
        # Send API key in headers.
        # https://bmo.readthedocs.io/en/latest/api/core/v1/general.html?highlight=x-bugzilla-api-key#authentication
        headers = kwargs.setdefault("headers", {})
        headers.setdefault("x-bugzilla-api-key", self.api_key)
        try:
            resp = self._client.request(verb, url, *args, **kwargs)
            resp.raise_for_status()
        except requests.HTTPError:
            logger.exception("%s %s", verb, url)
            raise
        parsed = resp.json()
        if parsed.get("error"):
            raise BugzillaClientError(parsed["message"])
        return parsed

    @instrumented_method
    def logged_in(self) -> bool:
        """Verify the API key validity."""
        # https://bugzilla.readthedocs.io/en/latest/api/core/v1/user.html#who-am-i
        try:
            resp = self._call("GET", f"{self.base_url}/rest/whoami")
        except (requests.HTTPError, BugzillaClientError):
            return False
        return "id" in resp

    @instrumented_method
    def get_bug(self, bugid) -> BugzillaBug:
        """Retrieve details about the specified bug id."""
        # https://bugzilla.readthedocs.io/en/latest/api/core/v1/bug.html#rest-single-bug
        url = f"{self.base_url}/rest/bug/{bugid}"
        bug_info = self._call("GET", url)
        parsed = BugzillaApiResponse.model_validate(bug_info)
        if not parsed.bugs:
            raise BugzillaClientError(
                f"Unexpected response content from 'GET {url}' (no 'bugs' field)"
            )
        bug = parsed.bugs[0]
        # If comment is private, then fetch it from server
        if bug.comment and bug.comment.is_private:
            comment_list = self.get_comments(bugid)
            matching_comments = [c for c in comment_list if c.id == bug.comment.id]
            # If no matching entry is found, set `bug.comment` to `None`.
            found = matching_comments[0] if matching_comments else None
            bug = bug.model_copy(update={"comment": found}, deep=True)
        return bug

    @instrumented_method
    def get_comments(self, bugid) -> list[BugzillaComment]:
        """Retrieve the list of comments of the specified bug id."""
        # https://bugzilla.readthedocs.io/en/latest/api/core/v1/comment.html#rest-comments
        url = f"{self.base_url}/rest/bug/{bugid}/comment"
        comments_info = self._call("GET", url)
        comments = comments_info.get("bugs", {}).get(str(bugid), {}).get("comments")
        if comments is None:
            raise BugzillaClientError(
                f"Unexpected response content from 'GET {url}' (no 'bugs' field)"
            )
        return BugzillaComments.validate_python(comments)

    @instrumented_method
    def update_bug(self, bugid, **fields) -> BugzillaBug:
        """Update the specified fields of the specified bug."""
        # https://bugzilla.readthedocs.io/en/latest/api/core/v1/bug.html#rest-update-bug
        url = f"{self.base_url}/rest/bug/{bugid}"
        updated_info = self._call("PUT", url, json=fields)
        parsed = BugzillaApiResponse.model_validate(updated_info)
        if not parsed.bugs:
            raise BugzillaClientError(
                f"Unexpected response content from 'PUT {url}' (no 'bugs' field)"
            )
        return parsed.bugs[0]

    @instrumented_method
    def list_webhooks(self):
        """List the currently configured webhooks, including their status."""
        url = f"{self.base_url}/rest/webhooks/list"
        webhooks_info = self._call("GET", url)
        parsed = BugzillaWebhooksResponse.model_validate(webhooks_info)
        if parsed.webhooks is None:
            raise BugzillaClientError(
                f"Unexpected response content from 'GET {url}' (no 'webhooks' field)"
            )
        return [wh for wh in parsed.webhooks if "/bugzilla_webhook" in wh.url]


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
