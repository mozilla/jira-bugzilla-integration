"""
Module for testing jbi/services.py
"""
from unittest import mock

import bugzilla
import pytest

from jbi.services import get_bugzilla, get_jira


def test_counter_is_incremented_on_jira_create_issue():
    jira_client = get_jira()

    with mock.patch("jbi.services.statsd") as mocked:
        jira_client.create_issue({})

    mocked.incr.assert_called_with("jbi.jira.methods.create_issue.count")


def test_timer_is_used_on_jira_create_issue():
    jira_client = get_jira()

    with mock.patch("jbi.services.statsd") as mocked:
        jira_client.create_issue({})

    mocked.timer.assert_called_with("jbi.jira.methods.create_issue.timer")


def test_timer_is_used_on_bugzilla_getcomments():
    bugzilla_client = get_bugzilla()

    with mock.patch("jbi.services.statsd") as mocked:
        bugzilla_client.get_comments([])

    mocked.timer.assert_called_with("jbi.bugzilla.methods.get_comments.timer")


def test_bugzilla_methods_are_retried_if_raising():
    with mock.patch(
        "jbi.services.rh_bugzilla.Bugzilla.return_value.get_comments"
    ) as mocked:
        mocked.side_effect = (bugzilla.BugzillaError("boom"), [mock.sentinel])
        bugzilla_client = get_bugzilla()

        # Not raising
        bugzilla_client.get_comments([])

    assert mocked.call_count == 2
