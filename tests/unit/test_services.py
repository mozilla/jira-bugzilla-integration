"""
Module for testing jbi/services.py
"""
from unittest import mock

import bugzilla
import pytest

from jbi.services import get_bugzilla, get_jira
from tests.fixtures.factories import comment_factory


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


def test_bugzilla_get_bug_comment(webhook_private_comment_example):
    # given
    with mock.patch("jbi.services.rh_bugzilla.Bugzilla") as mocked_bugzilla:
        mocked_bugzilla().getbug.return_value = webhook_private_comment_example.bug

        comments = [
            comment_factory(id=343, text="not this one", count=1),
            comment_factory(id=344, text="hello", count=2),
            comment_factory(id=345, text="not this one", count=3),
        ]
        mocked_bugzilla().get_comments.return_value = {
            "bugs": {
                str(webhook_private_comment_example.bug.id): {"comments": comments}
            },
            "comments": {},
        }

        expanded = get_bugzilla().getbug(webhook_private_comment_example.bug.id)

    # then
    assert expanded.comment["creator"] == "mathieu@mozilla.org"
    assert expanded.comment["text"] == "hello"


def test_bugzilla_missing_private_comment(
    webhook_private_comment_example,
):
    with mock.patch("jbi.services.rh_bugzilla.Bugzilla") as mocked_bugzilla:
        mocked_bugzilla().getbug.return_value = webhook_private_comment_example.bug

        mocked_bugzilla().get_comments.return_value = {
            "bugs": {str(webhook_private_comment_example.bug.id): {"comments": []}},
            "comments": {},
        }

        expanded = get_bugzilla().getbug(webhook_private_comment_example.bug.id)

    assert not expanded.comment
