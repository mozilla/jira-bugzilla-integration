"""
Module for testing src/jbi/services.py
"""
from unittest import mock

import pytest

from src.jbi.services import get_bugzilla, get_jira


def test_counter_is_incremented_on_jira_create_issue():
    with mock.patch("src.jbi.services.Jira.create_issue"):
        jira_client = get_jira()

        labelled_counter = jira_client.counters["jbi_jira_methods_total"].labels(
            method="create_issue"
        )
        with mock.patch.object(labelled_counter, "inc") as mocked:
            jira_client.create_issue({})

    assert mocked.called, "Counter was incremented on create_issue()"


def test_timer_is_used_on_jira_create_issue():
    with mock.patch("src.jbi.services.Jira.create_issue"):
        jira_client = get_jira()

        labelled_timer = jira_client.timers["jbi_jira_methods_milliseconds"].labels(
            method="create_issue"
        )
        with mock.patch.object(labelled_timer, "time") as mocked:
            jira_client.create_issue({})

    assert mocked.called, "Timer was used on create_issue()"


def test_timer_is_used_on_bugzilla_getcomments():
    with mock.patch("src.jbi.services.rh_bugzilla.Bugzilla"):
        bugzilla_client = get_bugzilla()

        labelled_timer = bugzilla_client.timers[
            "jbi_bugzilla_methods_milliseconds"
        ].labels(method="get_comments")
        with mock.patch.object(labelled_timer, "time") as mocked:
            bugzilla_client.get_comments([])

    assert mocked.called, "Timer was used on get_comments()"
