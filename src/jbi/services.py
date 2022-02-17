import bugzilla as rh_bugzilla  # type: ignore
from atlassian import Jira  # type: ignore

from src.app import environment

settings = environment.get_settings()

jira = Jira(
    url=settings.jira_base_url,
    username=settings.jira_username,
    password=settings.jira_password,
)

bugzilla = rh_bugzilla.Bugzilla(
    settings.bugzilla_base_url, api_key=settings.bugzilla_api_key
)


def bugzilla_check_health():
    health = {"up": bugzilla.logged_in}
    return health


def jira_check_health():
    server_info = jira.get_server_info(True)
    print(server_info)
    health = {"up": False}
    return health


def jbi_service_health_map():
    return {
        "bugzilla": bugzilla_check_health(),
        "jira": jira_check_health(),
    }
