"""
Services and functions that can be used to create custom actions
"""
import logging

import bugzilla as rh_bugzilla  # type: ignore
from atlassian import Jira  # type: ignore

from src.app import environment

settings = environment.get_settings()
services_logger = logging.getLogger("src.jbi.services")


def get_jira():
    return Jira(
        url=settings.jira_base_url,
        username=settings.jira_username,
        password=settings.jira_password,
    )


def get_bugzilla():
    return rh_bugzilla.Bugzilla(
        settings.bugzilla_base_url, api_key=settings.bugzilla_api_key
    )


def bugzilla_check_health():
    bugzilla = get_bugzilla()
    health = {"up": bugzilla.logged_in}
    return health


def jira_check_health():
    jira = get_jira()
    server_info = jira.get_server_info(True)
    services_logger.info(server_info)
    health = {"up": False}
    return health


def jbi_service_health_map():
    return {
        "bugzilla": bugzilla_check_health(),
        "jira": jira_check_health(),
    }
