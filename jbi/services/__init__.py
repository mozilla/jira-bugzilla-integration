from __future__ import annotations

from typing import TYPE_CHECKING

from . import bugzilla, jira

if TYPE_CHECKING:
    from jbi.models import Actions


def jbi_service_health_map(actions: Actions):
    """Returns dictionary of health check's for Bugzilla and Jira Services"""
    return {
        "bugzilla": bugzilla.bugzilla_check_health(),
        "jira": jira.jira_check_health(actions),
    }
