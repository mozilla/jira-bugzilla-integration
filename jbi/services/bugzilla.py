"""Contains a Bugzilla REST client and functions comprised of common operations
with that REST client
"""

import logging
from functools import lru_cache

import requests
from pydantic import parse_obj_as

from jbi import Operation, environment
from jbi.models import ActionContext, BugzillaApiResponse, BugzillaBug, BugzillaComment

from .common import InstrumentedClient, ServiceHealth

settings = environment.get_settings()

logger = logging.getLogger(__name__)


class BugzillaClientError(Exception):
    """Errors raised by `BugzillaClient`."""


class BugzillaClient:
    """A wrapper around `requests` to interact with a Bugzilla REST API."""

    def __init__(self, base_url, api_key):
        """Initialize the client, without network activity."""
        self.base_url = base_url
        self.api_key = api_key
        self._client = requests.Session()

    def _call(self, verb, url, *args, **kwargs):
        """Send HTTP requests with API key in querystring parameters."""
        # Send API key as querystring parameter.
        kwargs.setdefault("params", {}).setdefault("api_key", self.api_key)
        resp = self._client.request(verb, url, *args, **kwargs)
        resp.raise_for_status()
        parsed = resp.json()
        if parsed.get("error"):
            raise BugzillaClientError(parsed["message"])
        return parsed

    @property
    def logged_in(self) -> bool:
        """Verify the API key validity."""
        # https://bugzilla.readthedocs.io/en/latest/api/core/v1/user.html#who-am-i
        resp = self._call("GET", f"{self.base_url}/rest/whoami")
        return "id" in resp

    def get_bug(self, bugid) -> BugzillaBug:
        """Retrieve details about the specified bug id."""
        # https://bugzilla.readthedocs.io/en/latest/api/core/v1/bug.html#rest-single-bug
        url = f"{self.base_url}/rest/bug/{bugid}"
        bug_info = self._call("GET", url)
        parsed = BugzillaApiResponse.parse_obj(bug_info)
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
            bug = bug.copy(update={"comment": found})
        return bug

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
        return parse_obj_as(list[BugzillaComment], comments)

    def update_bug(self, bugid, **fields) -> BugzillaBug:
        """Update the specified fields of the specified bug."""
        # https://bugzilla.readthedocs.io/en/latest/api/core/v1/bug.html#rest-update-bug
        url = f"{self.base_url}/rest/bug/{bugid}"
        updated_info = self._call("PUT", url, json=fields)
        parsed = BugzillaApiResponse.parse_obj(updated_info)
        if not parsed.bugs:
            raise BugzillaClientError(
                f"Unexpected response content from 'PUT {url}' (no 'bugs' field)"
            )
        return parsed.bugs[0]


@lru_cache(maxsize=1)
def get_client():
    """Get bugzilla service"""
    bugzilla_client = BugzillaClient(
        settings.bugzilla_base_url, api_key=str(settings.bugzilla_api_key)
    )
    return InstrumentedClient(
        wrapped=bugzilla_client,
        prefix="bugzilla",
        methods=(
            "get_bug",
            "get_comments",
            "update_bugs",
        ),
        exceptions=(
            BugzillaClientError,
            requests.RequestException,
        ),
    )


def check_health() -> ServiceHealth:
    """Check health for Bugzilla Service"""
    client = get_client()
    health: ServiceHealth = {"up": client.logged_in}
    return health


def add_link_to_jira(context: ActionContext, bug: BugzillaBug, issue_key: str):
    """Add link to Jira in Bugzilla ticket"""
    jira_url = f"{settings.jira_base_url}browse/{issue_key}"
    logger.debug(
        "Link %r on Bug %s",
        jira_url,
        bug.id,
        extra=context.update(operation=Operation.LINK).dict(),
    )
    return get_client().update_bug(bug.id, see_also_add=jira_url)
