"""
Module for testing src/jbi/whiteboard_actions/default.py functionality
"""
# pylint: disable=cannot-enumerate-pytest-fixtures
from unittest import mock
from unittest.mock import MagicMock

import pytest
from src.jbi.bugzilla import BugzillaBug, BugzillaWebhookRequest

from src.jbi.whiteboard_actions import default


def test_default_invalid_init():
    with pytest.raises(TypeError):
        default.init()  # pylint: disable=no-value-for-parameter


def test_default_returns_callable_without_data():
    callable_object = default.init(whiteboard_tag="", jira_project_key="")
    assert callable_object
    with pytest.raises(TypeError):
        assert callable_object()


def test_default_returns_callable_with_data(webhook_request_example):

    with mock.patch("src.jbi.whiteboard_actions.default.get_jira") as mocked_jira:
        with mock.patch("src.jbi.whiteboard_actions.default.get_bugzilla") as mocked_bz:
            with mock.patch(
                "src.jbi.whiteboard_actions.default.getbug_as_bugzilla_object"
            ) as mocked_bz_func:
                mocked_bz_func.return_value = webhook_request_example.bug
                callable_object = default.init(whiteboard_tag="", jira_project_key="")
                assert callable_object
                value = callable_object(payload=webhook_request_example)

    assert value["status"] == "create"


def test_created_public():
    public_bug = BugzillaBug.parse_obj(
        {
            "assigned_to": "nobody@mozilla.org",
            "comment": None,
            "component": "General",
            "creator": "nobody@mozilla.org",
            "flags": [],
            "id": 654321,
            "is_private": False,
            "keywords": [],
            "priority": "",
            "product": "JBI",
            "resolution": "",
            "see_also": [],
            "severity": "--",
            "status": "NEW",
            "summary": "JBI Test",
            "type": "defect",
            "whiteboard": "[foo]",
        }
    )

    public_webhook = BugzillaWebhookRequest.parse_obj(
        {
            "bug": public_bug,
            "event": {
                "action": "create",
                "routing_key": "bug.create",
                "target": "bug",
                "time": "2022-06-30T14:03:02",
                "user": {
                    "id": 123456,
                    "login": "nobody@mozilla.org",
                    "real_name": "Nobody [ :nobody ]",
                },
            },
            "webhook_id": 34,
            "webhook_name": "local-test",
        }
    )

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
                mocked_bz_func.return_value = public_bug
                callable_object = default.init(
                    whiteboard_tag="", jira_project_key="JBI"
                )
                assert callable_object
                value = callable_object(payload=public_webhook)

    mock_jira_client.create_issue.assert_called_once_with(
        fields={
            "summary": "JBI Test",
            "labels": ["bugzilla", "foo", "[foo]"],
            "issuetype": {"name": "Bug"},
            "description": "Initial comment",
            "project": {"key": "JBI"},
        },
    )
    assert value["status"] == "create"


def test_created_private():
    private_bug = BugzillaBug.parse_obj(
        {
            "assigned_to": "nobody@mozilla.org",
            "comment": None,
            "component": "General",
            "creator": "nobody@mozilla.org",
            "flags": [],
            "id": 654321,
            "is_private": True,
            "keywords": [],
            "priority": "",
            "product": "JBI",
            "resolution": "",
            "see_also": [],
            "severity": "--",
            "status": "NEW",
            "summary": "JBI Test",
            "type": "defect",
            "whiteboard": "[foo]",
        }
    )

    private_webhook = BugzillaWebhookRequest.parse_obj(
        {
            "bug": {"id": 654321, "is_private": True},
            "event": {
                "action": "create",
                "routing_key": "bug.create",
                "target": "bug",
                "time": "2022-06-30T14:03:02",
                "user": {
                    "id": 123456,
                    "login": "nobody@mozilla.org",
                    "real_name": "Nobody [ :nobody ]",
                },
            },
            "webhook_id": 34,
            "webhook_name": "local-test",
        }
    )

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
                mocked_bz_func.return_value = private_bug
                callable_object = default.init(
                    whiteboard_tag="", jira_project_key="JBI"
                )
                assert callable_object
                value = callable_object(payload=private_webhook)

    mock_jira_client.create_issue.assert_called_once_with(
        fields={
            "summary": "JBI Test",
            "labels": ["bugzilla", "foo", "[foo]"],
            "issuetype": {"name": "Bug"},
            "description": "Initial comment",
            "project": {"key": "JBI"},
        },
    )
    assert value["status"] == "create"


def test_modified_public():
    public_bug = BugzillaBug.parse_obj(
        {
            "assigned_to": "nobody@mozilla.org",
            "comment": None,
            "component": "General",
            "creator": "nobody@mozilla.org",
            "flags": [],
            "id": 654321,
            "is_private": False,
            "keywords": [],
            "priority": "",
            "product": "JBI",
            "resolution": "",
            "see_also": ["https://mozilla.atlassian.net/browse/JBI-234"],
            "severity": "--",
            "status": "NEW",
            "summary": "JBI Test",
            "type": "defect",
            "whiteboard": "[foo]",
        }
    )

    public_webhook = BugzillaWebhookRequest.parse_obj(
        {
            "bug": public_bug,
            "event": {
                "action": "modify",
                "routing_key": "bug.modify:status",
                "target": "bug",
                "time": "2022-06-30T14:03:02",
                "user": {
                    "id": 123456,
                    "login": "nobody@mozilla.org",
                    "real_name": "Nobody [ :nobody ]",
                },
            },
            "webhook_id": 34,
            "webhook_name": "local-test",
        }
    )

    mock_jira_client = MagicMock()
    with mock.patch("src.jbi.whiteboard_actions.default.get_jira") as mocked_jira:
        mocked_jira.return_value = mock_jira_client
        with mock.patch("src.jbi.whiteboard_actions.default.get_bugzilla") as mocked_bz:
            with mock.patch(
                "src.jbi.whiteboard_actions.default.getbug_as_bugzilla_object"
            ) as mocked_bz_func:
                mocked_bz_func.return_value = public_bug
                callable_object = default.init(whiteboard_tag="", jira_project_key="")
                assert callable_object
                value = callable_object(payload=public_webhook)

    mock_jira_client.update_issue_field.assert_called_once_with(
        key="JBI-234",
        fields={"summary": "JBI Test", "labels": ["bugzilla", "foo", "[foo]"]},
    )
    assert value["status"] == "update"


def test_modified_private():
    private_bug = BugzillaBug.parse_obj(
        {
            "assigned_to": "nobody@mozilla.org",
            "comment": None,
            "component": "General",
            "creator": "nobody@mozilla.org",
            "flags": [],
            "id": 654321,
            "is_private": True,
            "keywords": [],
            "priority": "",
            "product": "JBI",
            "resolution": "",
            "see_also": ["https://mozilla.atlassian.net/browse/JBI-234"],
            "severity": "--",
            "status": "NEW",
            "summary": "JBI Test",
            "type": "defect",
            "whiteboard": "[foo]",
        }
    )

    private_webhook = BugzillaWebhookRequest.parse_obj(
        {
            "bug": {"id": 654321, "is_private": True},
            "event": {
                "action": "modify",
                "routing_key": "bug.modify:status",
                "target": "bug",
                "time": "2022-06-30T14:03:02",
                "user": {
                    "id": 123456,
                    "login": "nobody@mozilla.org",
                    "real_name": "Nobody [ :nobody ]",
                },
            },
            "webhook_id": 34,
            "webhook_name": "local-test",
        }
    )

    mock_jira_client = MagicMock()
    with mock.patch("src.jbi.whiteboard_actions.default.get_jira") as mocked_jira:
        mocked_jira.return_value = mock_jira_client
        with mock.patch("src.jbi.whiteboard_actions.default.get_bugzilla") as mocked_bz:
            with mock.patch(
                "src.jbi.whiteboard_actions.default.getbug_as_bugzilla_object"
            ) as mocked_bz_func:
                mocked_bz_func.return_value = private_bug
                callable_object = default.init(whiteboard_tag="", jira_project_key="")
                assert callable_object
                value = callable_object(payload=private_webhook)

    mock_jira_client.update_issue_field.assert_called_once_with(
        key="JBI-234",
        fields={"summary": "JBI Test", "labels": ["bugzilla", "foo", "[foo]"]},
    )
    assert value["status"] == "update"
