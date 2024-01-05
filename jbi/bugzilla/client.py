import logging

import requests

from jbi import environment
from jbi.common.instrument import instrument

from .models import (
    ApiResponse,
    Bug,
    BugzillaComments,
    Comment,
    WebhooksResponse,
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
    def get_bug(self, bugid) -> Bug:
        """Retrieve details about the specified bug id."""
        # https://bugzilla.readthedocs.io/en/latest/api/core/v1/bug.html#rest-single-bug
        url = f"{self.base_url}/rest/bug/{bugid}"
        bug_info = self._call("GET", url)
        parsed = ApiResponse.model_validate(bug_info)
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
    def get_comments(self, bugid) -> list[Comment]:
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
    def update_bug(self, bugid, **fields) -> Bug:
        """Update the specified fields of the specified bug."""
        # https://bugzilla.readthedocs.io/en/latest/api/core/v1/bug.html#rest-update-bug
        url = f"{self.base_url}/rest/bug/{bugid}"
        updated_info = self._call("PUT", url, json=fields)
        parsed = ApiResponse.model_validate(updated_info)
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
        parsed = WebhooksResponse.model_validate(webhooks_info)
        if parsed.webhooks is None:
            raise BugzillaClientError(
                f"Unexpected response content from 'GET {url}' (no 'webhooks' field)"
            )
        return [wh for wh in parsed.webhooks if "/bugzilla_webhook" in wh.url]
