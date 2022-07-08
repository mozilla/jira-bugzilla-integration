"""
Module for testing src/jbi/whiteboard_actions/extended.py functionality
"""
# pylint: disable=cannot-enumerate-pytest-fixtures
from unittest import mock
from unittest.mock import MagicMock

import pytest
from src.jbi.bugzilla import BugzillaBug, BugzillaWebhookRequest

from src.jbi.whiteboard_actions import default_with_assignee_and_status as action


def test_create_with_no_assignee(webhook_create_example):
    mock_jira_client = MagicMock()
    mock_bugzilla_client = MagicMock()
    mock_bugzilla_client.get_comments.return_value = {
        "bugs": {"654321": {"comments": [{"text": "Initial comment"}]}}
    }

    with mock.patch("src.jbi.whiteboard_actions.default.get_jira") as mocked_jira:
        mocked_jira.return_value = mock_jira_client
        with mock.patch("src.jbi.whiteboard_actions.default.get_bugzilla") as mocked_bz:
            mocked_bz.return_value = mock_bugzilla_client
            with mock.patch(
                "src.jbi.whiteboard_actions.default.getbug_as_bugzilla_object"
            ) as mocked_bz_func:
                mocked_bz_func.return_value = webhook_create_example.bug
                callable_object = action.init(whiteboard_tag="", jira_project_key="JBI")
                value = callable_object(payload=webhook_create_example)

    mock_jira_client.create_issue.assert_called_once_with(
        fields={
            "summary": "JBI Test",
            "labels": ["bugzilla", "devtest", "[devtest]"],
            "issuetype": {"name": "Bug"},
            "description": "Initial comment",
            "project": {"key": "JBI"},
        },
    )
    mock_jira_client.user_find_by_user_string.assert_not_called()
    mock_jira_client.update_issue_field.assert_not_called()
    mock_jira_client.set_issue_status.assert_not_called()
    assert value["status"] == "create"


def test_create_with_assignee(webhook_create_example):
    webhook_create_example.bug.assigned_to = "dtownsend@mozilla.com"

    mock_jira_client = MagicMock()
    mock_jira_client.create_issue.return_value = {"key": "JBI-534"}
    mock_jira_client.user_find_by_user_string.return_value = [{"accountId": "6254"}]

    mock_bugzilla_client = MagicMock()
    mock_bugzilla_client.get_comments.return_value = {
        "bugs": {"654321": {"comments": [{"text": "Initial comment"}]}}
    }

    with mock.patch("src.jbi.whiteboard_actions.default.get_jira") as mocked_jira:
        mocked_jira.return_value = mock_jira_client
        with mock.patch("src.jbi.whiteboard_actions.default.get_bugzilla") as mocked_bz:
            mocked_bz.return_value = mock_bugzilla_client
            with mock.patch(
                "src.jbi.whiteboard_actions.default.getbug_as_bugzilla_object"
            ) as mocked_bz_func:
                mocked_bz_func.return_value = webhook_create_example.bug
                callable_object = action.init(whiteboard_tag="", jira_project_key="JBI")
                value = callable_object(payload=webhook_create_example)

    mock_jira_client.create_issue.assert_called_once_with(
        fields={
            "summary": "JBI Test",
            "labels": ["bugzilla", "devtest", "[devtest]"],
            "issuetype": {"name": "Bug"},
            "description": "Initial comment",
            "project": {"key": "JBI"},
        },
    )
    mock_jira_client.user_find_by_user_string.assert_called_once_with(
        query="dtownsend@mozilla.com"
    )
    mock_jira_client.update_issue_field.assert_called_once_with(
        key="JBI-534",
        fields={"assignee": {"accountId": "6254"}},
    )
    mock_jira_client.set_issue_status.assert_not_called()
    assert value["status"] == "create"


def test_clear_assignee(webhook_create_example):
    webhook_create_example.bug.see_also = [
        "https://mozilla.atlassian.net/browse/JBI-234"
    ]
    webhook_create_example.event.action = "modify"
    webhook_create_example.event.routing_key = "bug.modify:assigned_to"

    mock_jira_client = MagicMock()

    mock_bugzilla_client = MagicMock()

    with mock.patch("src.jbi.whiteboard_actions.default.get_jira") as mocked_jira:
        mocked_jira.return_value = mock_jira_client
        with mock.patch("src.jbi.whiteboard_actions.default.get_bugzilla") as mocked_bz:
            mocked_bz.return_value = mock_bugzilla_client
            with mock.patch(
                "src.jbi.whiteboard_actions.default.getbug_as_bugzilla_object"
            ) as mocked_bz_func:
                mocked_bz_func.return_value = webhook_create_example.bug
                callable_object = action.init(whiteboard_tag="", jira_project_key="JBI")
                value = callable_object(payload=webhook_create_example)

    mock_jira_client.create_issue.assert_not_called()
    mock_jira_client.user_find_by_user_string.assert_not_called()
    mock_jira_client.update_issue_field.assert_any_call(
        key="JBI-234",
        fields={
            "summary": "JBI Test",
            "labels": ["bugzilla", "devtest", "[devtest]"],
        },
    )
    mock_jira_client.update_issue_field.assert_any_call(
        key="JBI-234",
        fields={"assignee": None},
    )
    mock_jira_client.set_issue_status.assert_not_called()
    assert value["status"] == "update"


def test_set_assignee(webhook_create_example):
    webhook_create_example.bug.assigned_to = "dtownsend@mozilla.com"
    webhook_create_example.bug.see_also = [
        "https://mozilla.atlassian.net/browse/JBI-234"
    ]
    webhook_create_example.event.action = "modify"
    webhook_create_example.event.routing_key = "bug.modify:assigned_to"

    mock_jira_client = MagicMock()
    mock_jira_client.user_find_by_user_string.return_value = [{"accountId": "6254"}]

    mock_bugzilla_client = MagicMock()

    with mock.patch("src.jbi.whiteboard_actions.default.get_jira") as mocked_jira:
        mocked_jira.return_value = mock_jira_client
        with mock.patch("src.jbi.whiteboard_actions.default.get_bugzilla") as mocked_bz:
            mocked_bz.return_value = mock_bugzilla_client
            with mock.patch(
                "src.jbi.whiteboard_actions.default.getbug_as_bugzilla_object"
            ) as mocked_bz_func:
                mocked_bz_func.return_value = webhook_create_example.bug
                callable_object = action.init(whiteboard_tag="", jira_project_key="JBI")
                value = callable_object(payload=webhook_create_example)

    mock_jira_client.create_issue.assert_not_called()
    mock_jira_client.user_find_by_user_string.assert_called_once_with(
        query="dtownsend@mozilla.com"
    )
    mock_jira_client.update_issue_field.assert_any_call(
        key="JBI-234",
        fields={
            "summary": "JBI Test",
            "labels": ["bugzilla", "devtest", "[devtest]"],
        },
    )
    mock_jira_client.update_issue_field.assert_any_call(
        key="JBI-234",
        fields={"assignee": {"accountId": "6254"}},
    )
    mock_jira_client.set_issue_status.assert_not_called()
    assert value["status"] == "update"


def test_create_with_unknown_status(webhook_create_example):
    webhook_create_example.bug.status = "NEW"
    webhook_create_example.bug.resolution = ""

    mock_jira_client = MagicMock()
    mock_bugzilla_client = MagicMock()
    mock_bugzilla_client.get_comments.return_value = {
        "bugs": {"654321": {"comments": [{"text": "Initial comment"}]}}
    }

    with mock.patch("src.jbi.whiteboard_actions.default.get_jira") as mocked_jira:
        mocked_jira.return_value = mock_jira_client
        with mock.patch("src.jbi.whiteboard_actions.default.get_bugzilla") as mocked_bz:
            mocked_bz.return_value = mock_bugzilla_client
            with mock.patch(
                "src.jbi.whiteboard_actions.default.getbug_as_bugzilla_object"
            ) as mocked_bz_func:
                mocked_bz_func.return_value = webhook_create_example.bug
                callable_object = action.init(
                    whiteboard_tag="",
                    jira_project_key="JBI",
                    status_map={
                        "ASSIGNED": "In Progress",
                        "FIXED": "Closed",
                    },
                )
                value = callable_object(payload=webhook_create_example)

    mock_jira_client.create_issue.assert_called_once_with(
        fields={
            "summary": "JBI Test",
            "labels": ["bugzilla", "devtest", "[devtest]"],
            "issuetype": {"name": "Bug"},
            "description": "Initial comment",
            "project": {"key": "JBI"},
        },
    )
    mock_jira_client.user_find_by_user_string.assert_not_called()
    mock_jira_client.update_issue_field.assert_not_called()
    mock_jira_client.set_issue_status.assert_not_called()
    assert value["status"] == "create"


def test_create_with_known_status(webhook_create_example):
    webhook_create_example.bug.status = "ASSIGNED"
    webhook_create_example.bug.resolution = ""

    mock_jira_client = MagicMock()
    mock_jira_client.create_issue.return_value = {"key": "JBI-534"}

    mock_bugzilla_client = MagicMock()
    mock_bugzilla_client.get_comments.return_value = {
        "bugs": {"654321": {"comments": [{"text": "Initial comment"}]}}
    }

    with mock.patch("src.jbi.whiteboard_actions.default.get_jira") as mocked_jira:
        mocked_jira.return_value = mock_jira_client
        with mock.patch("src.jbi.whiteboard_actions.default.get_bugzilla") as mocked_bz:
            mocked_bz.return_value = mock_bugzilla_client
            with mock.patch(
                "src.jbi.whiteboard_actions.default.getbug_as_bugzilla_object"
            ) as mocked_bz_func:
                mocked_bz_func.return_value = webhook_create_example.bug
                callable_object = action.init(
                    whiteboard_tag="",
                    jira_project_key="JBI",
                    status_map={
                        "ASSIGNED": "In Progress",
                        "FIXED": "Closed",
                    },
                )
                value = callable_object(payload=webhook_create_example)

    mock_jira_client.create_issue.assert_called_once_with(
        fields={
            "summary": "JBI Test",
            "labels": ["bugzilla", "devtest", "[devtest]"],
            "issuetype": {"name": "Bug"},
            "description": "Initial comment",
            "project": {"key": "JBI"},
        },
    )
    mock_jira_client.user_find_by_user_string.assert_not_called()
    mock_jira_client.update_issue_field.assert_not_called()
    mock_jira_client.set_issue_status.assert_called_once_with("JBI-534", "In Progress")
    assert value["status"] == "create"


def test_change_to_unknown_status(webhook_create_example):
    webhook_create_example.bug.status = "NEW"
    webhook_create_example.bug.resolution = ""
    webhook_create_example.bug.see_also = [
        "https://mozilla.atlassian.net/browse/JBI-234"
    ]
    webhook_create_example.event.action = "modify"
    webhook_create_example.event.routing_key = "bug.modify:status"

    mock_jira_client = MagicMock()

    mock_bugzilla_client = MagicMock()

    with mock.patch("src.jbi.whiteboard_actions.default.get_jira") as mocked_jira:
        mocked_jira.return_value = mock_jira_client
        with mock.patch("src.jbi.whiteboard_actions.default.get_bugzilla") as mocked_bz:
            mocked_bz.return_value = mock_bugzilla_client
            with mock.patch(
                "src.jbi.whiteboard_actions.default.getbug_as_bugzilla_object"
            ) as mocked_bz_func:
                mocked_bz_func.return_value = webhook_create_example.bug
                callable_object = action.init(
                    whiteboard_tag="",
                    jira_project_key="JBI",
                    status_map={
                        "ASSIGNED": "In Progress",
                        "FIXED": "Closed",
                    },
                )
                value = callable_object(payload=webhook_create_example)

    mock_jira_client.create_issue.assert_not_called()
    mock_jira_client.user_find_by_user_string.assert_not_called()
    mock_jira_client.update_issue_field.assert_called_once_with(
        key="JBI-234",
        fields={
            "summary": "JBI Test",
            "labels": ["bugzilla", "devtest", "[devtest]"],
        },
    )
    mock_jira_client.set_issue_status.assert_not_called()
    assert value["status"] == "update"


def test_change_to_known_status(webhook_create_example):
    webhook_create_example.bug.status = "ASSIGNED"
    webhook_create_example.bug.resolution = ""
    webhook_create_example.bug.see_also = [
        "https://mozilla.atlassian.net/browse/JBI-234"
    ]
    webhook_create_example.event.action = "modify"
    webhook_create_example.event.routing_key = "bug.modify:status"

    mock_jira_client = MagicMock()

    mock_bugzilla_client = MagicMock()

    with mock.patch("src.jbi.whiteboard_actions.default.get_jira") as mocked_jira:
        mocked_jira.return_value = mock_jira_client
        with mock.patch("src.jbi.whiteboard_actions.default.get_bugzilla") as mocked_bz:
            mocked_bz.return_value = mock_bugzilla_client
            with mock.patch(
                "src.jbi.whiteboard_actions.default.getbug_as_bugzilla_object"
            ) as mocked_bz_func:
                mocked_bz_func.return_value = webhook_create_example.bug
                callable_object = action.init(
                    whiteboard_tag="",
                    jira_project_key="JBI",
                    status_map={
                        "ASSIGNED": "In Progress",
                        "FIXED": "Closed",
                    },
                )
                value = callable_object(payload=webhook_create_example)

    mock_jira_client.create_issue.assert_not_called()
    mock_jira_client.user_find_by_user_string.assert_not_called()
    mock_jira_client.update_issue_field.assert_called_once_with(
        key="JBI-234",
        fields={
            "summary": "JBI Test",
            "labels": ["bugzilla", "devtest", "[devtest]"],
        },
    )
    mock_jira_client.set_issue_status.assert_called_once_with("JBI-234", "In Progress")
    assert value["status"] == "update"


def test_change_to_known_resolution(webhook_create_example):
    webhook_create_example.bug.status = "RESOLVED"
    webhook_create_example.bug.resolution = "FIXED"
    webhook_create_example.bug.see_also = [
        "https://mozilla.atlassian.net/browse/JBI-234"
    ]
    webhook_create_example.event.action = "modify"
    webhook_create_example.event.routing_key = "bug.modify:resolution"

    mock_jira_client = MagicMock()

    mock_bugzilla_client = MagicMock()

    with mock.patch("src.jbi.whiteboard_actions.default.get_jira") as mocked_jira:
        mocked_jira.return_value = mock_jira_client
        with mock.patch("src.jbi.whiteboard_actions.default.get_bugzilla") as mocked_bz:
            mocked_bz.return_value = mock_bugzilla_client
            with mock.patch(
                "src.jbi.whiteboard_actions.default.getbug_as_bugzilla_object"
            ) as mocked_bz_func:
                mocked_bz_func.return_value = webhook_create_example.bug
                callable_object = action.init(
                    whiteboard_tag="",
                    jira_project_key="JBI",
                    status_map={
                        "ASSIGNED": "In Progress",
                        "FIXED": "Closed",
                    },
                )
                value = callable_object(payload=webhook_create_example)

    mock_jira_client.create_issue.assert_not_called()
    mock_jira_client.user_find_by_user_string.assert_not_called()
    mock_jira_client.update_issue_field.assert_called_once_with(
        key="JBI-234",
        fields={
            "summary": "JBI Test",
            "labels": ["bugzilla", "devtest", "[devtest]"],
        },
    )
    mock_jira_client.set_issue_status.assert_called_once_with("JBI-234", "Closed")
    assert value["status"] == "update"
