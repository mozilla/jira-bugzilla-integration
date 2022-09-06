"""
Module for testing jbi/actions/default.py functionality
"""
# pylint: disable=cannot-enumerate-pytest-fixtures
from unittest import mock

import pytest

from jbi.actions import default
from jbi.models import BugzillaWebhookRequest
from jbi.services.jira import JiraCreateError
from tests.fixtures.factories import bug_factory, comment_factory


def test_default_invalid_init():
    with pytest.raises(TypeError):
        default.init()  # pylint: disable=no-value-for-parameter


def test_default_returns_callable_without_data(mocked_bugzilla, mocked_jira):
    callable_object = default.init(jira_project_key="")
    assert callable_object
    with pytest.raises(TypeError) as exc_info:
        assert callable_object()

    assert "missing 2 required positional arguments: 'bug' and 'event'" in str(
        exc_info.value
    )


def test_default_returns_callable_with_data(
    webhook_create_example, mocked_jira, mocked_bugzilla
):
    sentinel = mock.sentinel
    mocked_jira.create_issue.return_value = {"key": "k"}
    mocked_jira.create_or_update_issue_remote_links.return_value = sentinel
    mocked_bugzilla.get_bug.return_value = webhook_create_example.bug
    callable_object = default.init(jira_project_key="")

    handled, details = callable_object(
        bug=webhook_create_example.bug, event=webhook_create_example.event
    )

    assert handled
    assert details["responses"][-1] == sentinel


def test_created_public(
    webhook_create_example: BugzillaWebhookRequest, mocked_jira, mocked_bugzilla
):
    mocked_jira.create_issue.return_value = {"key": "k"}
    mocked_bugzilla.get_bug.return_value = webhook_create_example.bug
    mocked_bugzilla.get_comments.return_value = [
        comment_factory(text="Initial comment")
    ]
    callable_object = default.init(jira_project_key="JBI")

    callable_object(bug=webhook_create_example.bug, event=webhook_create_example.event)

    mocked_jira.create_issue.assert_called_once_with(
        fields={
            "summary": "JBI Test",
            "labels": ["bugzilla", "devtest", "[devtest]"],
            "issuetype": {"name": "Bug"},
            "description": "Initial comment",
            "project": {"key": "JBI"},
        },
    )


def test_modified_public(webhook_modify_example: BugzillaWebhookRequest, mocked_jira):
    assert webhook_modify_example.bug
    callable_object = default.init(jira_project_key="")

    callable_object(bug=webhook_modify_example.bug, event=webhook_modify_example.event)

    assert webhook_modify_example.bug.extract_from_see_also(), "see_also is not empty"

    mocked_jira.update_issue_field.assert_called_once_with(
        key="JBI-234",
        fields={"summary": "JBI Test", "labels": ["bugzilla", "devtest", "[devtest]"]},
    )


def test_comment_for_modified_assignee_and_status(
    webhook_change_status_assignee, mocked_jira
):
    webhook_change_status_assignee.bug.see_also = [
        "https://mozilla.atlassian.net/browse/JBI-234"
    ]

    callable_object = default.init(jira_project_key="")

    callable_object(
        bug=webhook_change_status_assignee.bug,
        event=webhook_change_status_assignee.event,
    )

    mocked_jira.issue_add_comment.assert_any_call(
        issue_key="JBI-234",
        comment='{\n    "assignee": "nobody@mozilla.org"\n}',
    )
    mocked_jira.issue_add_comment.assert_any_call(
        issue_key="JBI-234",
        comment='{\n    "modified by": "nobody@mozilla.org",\n    "resolution": "",\n    "status": "NEW"\n}',
    )


def test_added_comment(webhook_comment_example: BugzillaWebhookRequest, mocked_jira):

    callable_object = default.init(jira_project_key="")

    callable_object(
        bug=webhook_comment_example.bug, event=webhook_comment_example.event
    )

    mocked_jira.issue_add_comment.assert_called_once_with(
        issue_key="JBI-234",
        comment="*(mathieu@mozilla.org)* commented: \n{quote}hello{quote}",
    )


def test_added_comment_without_linked_issue(
    webhook_comment_example: BugzillaWebhookRequest, mocked_jira
):
    assert webhook_comment_example.bug
    webhook_comment_example.bug.see_also = []
    callable_object = default.init(jira_project_key="")

    handled, _ = callable_object(
        bug=webhook_comment_example.bug, event=webhook_comment_example.event
    )

    assert not handled


def test_jira_returns_an_error(
    webhook_create_example: BugzillaWebhookRequest, mocked_jira
):
    mocked_jira.create_issue.return_value = [
        {"errors": ["Boom"]},
    ]
    callable_object = default.init(jira_project_key="")

    with pytest.raises(JiraCreateError) as exc_info:
        callable_object(
            bug=webhook_create_example.bug, event=webhook_create_example.event
        )

    assert str(exc_info.value) == "Boom"


def test_disabled_label_field(
    webhook_create_example: BugzillaWebhookRequest, mocked_jira, mocked_bugzilla
):
    mocked_jira.create_issue.return_value = {"key": "k"}
    mocked_bugzilla.get_bug.return_value = webhook_create_example.bug
    mocked_bugzilla.get_comments.return_value = [
        comment_factory(text="Initial comment")
    ]
    callable_object = default.init(jira_project_key="JBI", sync_whiteboard_labels=False)

    callable_object(bug=webhook_create_example.bug, event=webhook_create_example.event)

    mocked_jira.create_issue.assert_called_once_with(
        fields={
            "summary": "JBI Test",
            "issuetype": {"name": "Bug"},
            "description": "Initial comment",
            "project": {"key": "JBI"},
        },
    )
