"""
Module for testing src/jbi/whiteboard_actions/default.py functionality
"""
# pylint: disable=cannot-enumerate-pytest-fixtures
from unittest import mock
from unittest.mock import MagicMock

import pytest

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
