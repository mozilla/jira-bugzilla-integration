"""Services and functions that can be used to create custom actions"""
import bugzilla as rh_bugzilla  # type: ignore
from atlassian import Jira  # type: ignore

from src.app import environment

settings = environment.get_settings()


def get_jira():
    """Get atlassian Jira Service"""
    return Jira(
        url=settings.jira_base_url,
        username=settings.jira_username,
        password=settings.jira_api_key,  # package calls this param 'password' but actually expects an api key
        cloud=True,  # we run against an instance of Jira cloud
    )


def get_bugzilla():
    """Get bugzilla service"""
    return rh_bugzilla.Bugzilla(
        settings.bugzilla_base_url, api_key=str(settings.bugzilla_api_key)
    )


def bugzilla_check_health():
    """Check health for Bugzilla Service"""
    bugzilla = get_bugzilla()
    health = {"up": bugzilla.logged_in}
    return health


def jira_check_health():
    """Check health for Jira Service"""
    jira = get_jira()
    server_info = jira.get_server_info(True)
    health = {"up": server_info is not None}
    return health


def jbi_service_health_map():
    """Returns dictionary of health check's for Bugzilla and Jira Services"""
    return {
        "bugzilla": bugzilla_check_health(),
        "jira": jira_check_health(),
    }
