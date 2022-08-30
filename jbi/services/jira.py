"""Contains a Jira REST client and functions comprised of common operations
with that REST client
"""

from __future__ import annotations

import concurrent.futures
import logging
from functools import lru_cache
from typing import TYPE_CHECKING

from atlassian import Jira, errors

from jbi import environment

from .common import InstrumentedClient, ServiceHealth

if TYPE_CHECKING:
    from jbi.models import Actions

settings = environment.get_settings()

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_client():
    """Get atlassian Jira Service"""
    jira_client = Jira(
        url=settings.jira_base_url,
        username=settings.jira_username,
        password=settings.jira_api_key,  # package calls this param 'password' but actually expects an api key
        cloud=True,  # we run against an instance of Jira cloud
    )

    return InstrumentedClient(
        wrapped=jira_client,
        prefix="jira",
        methods=(
            "update_issue_field",
            "set_issue_status",
            "issue_add_comment",
            "create_issue",
        ),
        exceptions=(errors.ApiError,),
    )


def fetch_visible_projects() -> list[dict]:
    """Return list of projects that are visible with the configured Jira credentials"""
    client = get_client()
    projects: list[dict] = client.projects(included_archived=None)
    return projects


def check_health(actions: Actions) -> ServiceHealth:
    """Check health for Jira Service"""
    client = get_client()
    server_info = client.get_server_info(True)
    is_up = server_info is not None
    health: ServiceHealth = {
        "up": is_up,
        "all_projects_are_visible": is_up and _all_projects_visible(actions),
        "all_projects_have_permissions": _all_projects_permissions(actions),
    }
    return health


def _all_projects_visible(actions: Actions) -> bool:
    visible_projects = {project["key"] for project in fetch_visible_projects()}
    missing_projects = actions.configured_jira_projects_keys - visible_projects
    if missing_projects:
        logger.error(
            "Jira projects %s are not visible with configured credentials",
            missing_projects,
        )
    return not missing_projects


def _all_projects_permissions(actions: Actions):
    """Fetches and validates that required permissions exist for the configured projects"""
    all_projects_perms = _fetch_project_permissions(actions)
    return _validate_permissions(all_projects_perms)


def _fetch_project_permissions(actions):
    """Fetches permissions for the configured projects"""
    required_perms_by_project = {
        action.parameters["jira_project_key"]: action.required_jira_permissions
        for action in actions
        if "jira_project_key" in action.parameters
    }
    client = get_client()
    all_projects_perms = {}
    # Query permissions for all configured projects in parallel threads.
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures_to_projects = {
            executor.submit(
                client.get_permissions,
                project_key=project_key,
                permissions=",".join(required_permissions),
            ): project_key
            for project_key, required_permissions in required_perms_by_project.items()
        }
        # Obtain futures' results unordered.
        for future in concurrent.futures.as_completed(futures_to_projects):
            project_key = futures_to_projects[future]
            response = future.result()
            all_projects_perms[project_key] = (
                required_perms_by_project[project_key],
                response["permissions"],
            )
    return all_projects_perms


def _validate_permissions(all_projects_perms):
    """Validates permissions for the configured projects"""
    misconfigured = []
    for project_key, (required_perms, obtained_perms) in all_projects_perms.items():
        missing = required_perms - set(obtained_perms.keys())
        not_given = set(
            entry["key"]
            for entry in obtained_perms.values()
            if not entry["havePermission"]
        )
        if missing | not_given:
            misconfigured.append((project_key, missing | not_given))
    for project_key, missing in misconfigured:
        logger.error(
            "Configured credentials don't have permissions %s on Jira project %s",
            ",".join(missing),
            project_key,
            extra={
                "jira": {
                    "project": project_key,
                }
            },
        )
    return not misconfigured
