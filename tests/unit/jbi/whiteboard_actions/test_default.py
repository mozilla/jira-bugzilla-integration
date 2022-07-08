"""
Module for testing src/jbi/whiteboard_actions/default.py functionality
"""
# pylint: disable=cannot-enumerate-pytest-fixtures
from unittest import mock
from unittest.mock import MagicMock

import pytest
from src.jbi.bugzilla import BugzillaBug, BugzillaWebhookRequest
from src.jbi.errors import ActionError
from src.jbi.whiteboard_actions import default


def test_default_invalid_init():
    with pytest.raises(TypeError):
        default.init()  # pylint: disable=no-value-for-parameter


def test_default_returns_callable_without_data(mocked_bugzilla, mocked_jira):
    callable_object = default.init(whiteboard_tag="", jira_project_key="")
    assert callable_object
    with pytest.raises(TypeError) as exc_info:
        assert callable_object()

    assert "missing 1 required positional argument: 'payload'" in str(exc_info.value)


def test_default_returns_callable_with_data(webhook_create_example, mocked_jira):
    callable_object = default.init(whiteboard_tag="", jira_project_key="")

    value = callable_object(payload=webhook_create_example)

    assert value["status"] == "create"


def test_created_public(webhook_create_example: BugzillaWebhookRequest, mocked_jira):
    callable_object = default.init(whiteboard_tag="", jira_project_key="JBI")

    value = callable_object(payload=webhook_create_example)

    mocked_jira().create_issue.assert_called_once_with(
        fields={
            "summary": "JBI Test",
            "labels": ["bugzilla", "devtest", "[devtest]"],
            "issuetype": {"name": "Bug"},
            "description": "Initial comment",
            "project": {"key": "JBI"},
        },
    )
    assert value["status"] == "create"


def test_created_private(
    webhook_create_private_example: BugzillaWebhookRequest, mocked_jira
):
    callable_object = default.init(whiteboard_tag="", jira_project_key="JBI")

    value = callable_object(payload=webhook_create_private_example)

    mocked_jira().create_issue.assert_called_once_with(
        fields={
            "summary": "JBI Test",
            "labels": ["bugzilla", "devtest", "[devtest]"],
            "issuetype": {"name": "Bug"},
            "description": "Initial comment",
            "project": {"key": "JBI"},
        },
    )
    assert value["status"] == "create"


def test_modified_public(webhook_modify_example: BugzillaWebhookRequest, mocked_jira):
    callable_object = default.init(whiteboard_tag="", jira_project_key="")

    value = callable_object(payload=webhook_modify_example)

    assert webhook_modify_example.bug.extract_from_see_also(), "see_also is not empty"

    mocked_jira().update_issue_field.assert_called_once_with(
        key="JBI-234",
        fields={"summary": "JBI Test", "labels": ["bugzilla", "devtest", "[devtest]"]},
    )
    assert value["status"] == "update"


def test_modified_private(
    webhook_modify_private_example: BugzillaWebhookRequest, mocked_jira
):
    callable_object = default.init(whiteboard_tag="", jira_project_key="")

    value = callable_object(payload=webhook_modify_private_example)

    mocked_jira().update_issue_field.assert_called_once_with(
        key="JBI-234",
        fields={"summary": "JBI Test", "labels": ["bugzilla", "devtest", "[devtest]"]},
    )
    assert value["status"] == "update"


def test_added_comment(webhook_comment_example: BugzillaWebhookRequest, mocked_jira):

    callable_object = default.init(whiteboard_tag="", jira_project_key="")

    value = callable_object(payload=webhook_comment_example)

    mocked_jira().issue_add_comment.assert_called_once_with(
        issue_key="JBI-234",
        comment="*(mathieu@mozilla.org)* commented: \n{quote}hello{quote}",
    )
    assert value["status"] == "comment"


def test_added_comment_without_linked_issue(
    webhook_comment_example: BugzillaWebhookRequest, mocked_jira
):
    webhook_comment_example.bug.see_also = []
    callable_object = default.init(whiteboard_tag="", jira_project_key="")

    value = callable_object(payload=webhook_comment_example)

    assert value["status"] == "noop"


def test_jira_returns_an_error(
    webhook_create_example: BugzillaWebhookRequest, mocked_jira
):
    mocked_jira().create_issue.return_value = [
        {"errors": ["Boom"]},
    ]
    callable_object = default.init(whiteboard_tag="", jira_project_key="")

    with pytest.raises(ActionError) as exc_info:
        callable_object(payload=webhook_create_example)

    assert str(exc_info.value) == "response contains error: {'errors': ['Boom']}"
