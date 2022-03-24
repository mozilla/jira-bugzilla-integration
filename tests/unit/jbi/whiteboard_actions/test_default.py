"""
Module for testing src/jbi/whiteboard_actions/default.py functionality
"""
# pylint: disable=cannot-enumerate-pytest-fixtures
import bugzilla  # type: ignore
import pytest
from atlassian import Jira  # type: ignore

from src.jbi.bugzilla_objects import BugzillaWebhookRequest
from src.jbi.whiteboard_actions import default


def test_default_invalid_init():
    with pytest.raises(TypeError):
        default.init()  # pylint: disable=no-value-for-parameter


def test_default_returns_callable_without_data():
    callable_object = default.init(whiteboard_tag="", jira_project_key="")
    assert callable_object
    with pytest.raises(TypeError):
        assert callable_object()


def test_default_returns_callable_with_data(webhook_request_example, monkeypatch):
    callable_object = default.init(whiteboard_tag="", jira_project_key="")
    assert callable_object

    def mock_getbug(self, id):
        class MockBug:
            __dict__ = webhook_request_example["bug"]

        return MockBug()

    def mock_update_bugs(self, id, updates):
        return {}

    def mock_create_issue(self, fields):
        return {}

    def mock_create_or_update_issue_remote_links(self, issue_key, link_url, title):
        return {}

    monkeypatch.setattr(bugzilla.Bugzilla, "getbug", mock_getbug)
    monkeypatch.setattr(bugzilla.Bugzilla, "update_bugs", mock_update_bugs)
    monkeypatch.setattr(Jira, "create_issue", mock_create_issue)
    monkeypatch.setattr(
        Jira,
        "create_or_update_issue_remote_links",
        mock_create_or_update_issue_remote_links,
    )
    try:
        callable_object(payload=webhook_request_example)
    except Exception as exception:  # pylint: disable=broad-except
        assert False, f"`default` raised an exception {exception}"
