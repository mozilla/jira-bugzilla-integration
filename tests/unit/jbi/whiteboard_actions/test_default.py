"""
Module for testing src/jbi/whiteboard_actions/default.py functionality
"""
# pylint: disable=cannot-enumerate-pytest-fixtures
import pytest

from src.jbi.whiteboard_actions import default


def test_default_invalid_init():  # pylint: disable=missing-function-docstring
    with pytest.raises(TypeError):
        default.init()  # pylint: disable=no-value-for-parameter


def test_default_returns_callable_without_data():  # pylint: disable=missing-function-docstring
    callable_object = default.init(whiteboard_tag="", jira_project_key="")
    assert callable_object
    with pytest.raises(TypeError):
        assert callable_object()


def test_default_returns_callable_with_data():  # pylint: disable=missing-function-docstring
    callable_object = default.init(whiteboard_tag="", jira_project_key="")
    assert callable_object
    try:
        callable_object(payload={})
    except Exception as exception:  # pylint: disable=broad-except
        assert False, f"`default` raised an exception {exception}"
