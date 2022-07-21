"""Services and functions that can be used to create custom actions"""
import logging
from typing import Dict, List

import bugzilla as rh_bugzilla
from atlassian import Jira

from src.app import environment
from src.jbi.models import Actions

settings = environment.get_settings()

logger = logging.getLogger(__name__)


ServiceHealth = Dict[str, bool]


def get_jira():
    """Get atlassian Jira Service"""
    return Jira(
        url=settings.jira_base_url,
        username=settings.jira_username,
        password=settings.jira_api_key,  # package calls this param 'password' but actually expects an api key
        cloud=True,  # we run against an instance of Jira cloud
    )


def jira_visible_projects(jira=None) -> List[Dict]:
    """Return list of projects that are visible with the configured Jira credentials"""
    jira = jira or get_jira()
    projects: List[Dict] = jira.projects(included_archived=None)
    return projects


def get_bugzilla():
    """Get bugzilla service"""
    return rh_bugzilla.Bugzilla(
        settings.bugzilla_base_url, api_key=str(settings.bugzilla_api_key)
    )


def _bugzilla_check_health() -> ServiceHealth:
    """Check health for Bugzilla Service"""
    bugzilla = get_bugzilla()
    health: ServiceHealth = {"up": bugzilla.logged_in}
    return health


def _jira_check_health(actions: Actions) -> ServiceHealth:
    """Check health for Jira Service"""
    jira = get_jira()
    server_info = jira.get_server_info(True)
    is_up = server_info is not None
    health: ServiceHealth = {
        "up": is_up,
        "all_projects_are_visible": is_up and _all_jira_projects_visible(jira, actions),
    }
    return health


def _all_jira_projects_visible(jira, actions: Actions) -> bool:
    visible_projects = {project["key"] for project in jira_visible_projects(jira)}
    missing_projects = actions.configured_jira_projects_keys - visible_projects
    if missing_projects:
        logger.error(
            "Jira projects %s are not visible with configured credentials",
            missing_projects,
        )
    return not missing_projects


def jbi_service_health_map(actions: Actions):
    """Returns dictionary of health check's for Bugzilla and Jira Services"""
    return {
        "bugzilla": _bugzilla_check_health(),
        "jira": _jira_check_health(actions),
    }
