import logging
from typing import Any, Collection, Iterable, Optional, Union

import requests
from atlassian import Jira
from atlassian import errors as atlassian_errors
from atlassian.rest_client import log as atlassian_logger
from requests import exceptions as requests_exceptions

from jbi import environment
from jbi.common.instrument import instrument

settings = environment.get_settings()

logger = logging.getLogger(__name__)


def fatal_code(exc):
    """Do not retry 4XX errors, mark them as fatal."""
    try:
        return 400 <= exc.response.status_code < 500
    except AttributeError:
        # `ApiError` or `ConnectionError` won't have response attribute.
        return False


instrumented_method = instrument(
    prefix="jira",
    exceptions=(
        atlassian_errors.ApiError,
        requests_exceptions.RequestException,
    ),
    giveup=fatal_code,
)


class JiraCreateError(Exception):
    """Error raised on Jira issue creation."""


class JiraClient(Jira):
    """Adapted Atlassian Jira client that logs errors and wraps methods
    in our instrumentation decorator.
    """

    def raise_for_status(self, *args, **kwargs):
        """Catch and log HTTP errors responses of the Jira self.client.

        Without this the actual requests and responses are not exposed when an error
        occurs, which makes troubleshooting tedious.
        """
        try:
            return super().raise_for_status(*args, **kwargs)
        except requests.HTTPError as exc:
            request = exc.request
            response = exc.response
            assert response is not None, f"HTTPError {exc} has no attached response"
            atlassian_logger.error(
                "HTTP: %s %s -> %s %s",
                request.method,
                request.path_url,
                response.status_code,
                response.reason,
                extra={"body": response.text},
            )
            # Set the exception message so that its str version contains details.
            msg = f"{request.method} {request.path_url} -> HTTP {response.status_code}: {exc}"
            exc.args = (msg,) + exc.args[1:]
            raise

    get_server_info = instrumented_method(Jira.get_server_info)
    get_project_components = instrumented_method(Jira.get_project_components)
    update_issue = instrumented_method(Jira.update_issue)
    update_issue_field = instrumented_method(Jira.update_issue_field)
    set_issue_status = instrumented_method(Jira.set_issue_status)
    issue_add_comment = instrumented_method(Jira.issue_add_comment)
    create_issue = instrumented_method(Jira.create_issue)
    get_project = instrumented_method(Jira.get_project)

    @instrumented_method
    def paginated_projects(
        self,
        included_archived=None,
        expand=None,
        url=None,
        keys: Optional[Collection[str]] = None,
    ) -> dict:
        """Returns a paginated list of projects visible to the user.

        https://developer.atlassian.com/cloud/jira/platform/rest/v2/api-group-projects/#api-rest-api-2-project-search-get

        We've patched this method of the Jira client to accept the `keys` param.
        """

        if not self.cloud:
            raise ValueError(
                "``projects_from_cloud`` method is only available for Jira Cloud platform"
            )

        params_dict: dict[str, Any] = {}

        if keys is not None:
            if len(keys) > 50:
                raise ValueError("Up to 50 project keys can be provided.")
            params_dict["keys"] = list(keys)

        if included_archived:
            params_dict["includeArchived"] = included_archived
        if expand:
            params_dict["expand"] = expand
        page_url = url or self.resource_url("project/search")
        is_url_absolute = bool(page_url.lower().startswith("http"))
        projects: Union[dict, None] = self.get(
            page_url, params=params_dict, absolute=is_url_absolute
        )
        return projects if projects else {"values": []}

    @instrumented_method
    def permitted_projects(self, permissions: Optional[Iterable] = None) -> list[dict]:
        """Fetches projects that the user has the required permissions for

        https://developer.atlassian.com/cloud/jira/platform/rest/v2/api-group-permissions/#api-rest-api-2-permissions-project-post
        """
        if permissions is None:
            permissions = []

        response = self.post(
            "/rest/api/2/permissions/project",
            json={"permissions": list(permissions)},
        )
        projects: list[dict] = response["projects"] if response else []
        return projects
