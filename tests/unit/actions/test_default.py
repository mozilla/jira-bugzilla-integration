from unittest import mock

import pytest

from jbi.actions import default
from jbi.models import ActionContext


def test_default_invalid_init():
    with pytest.raises(TypeError):
        default.init()  # pylint: disable=no-value-for-parameter


def test_default_invalid_operation():
    with pytest.raises(ValueError):
        default.init(jira_project_key="", steps={"bad-operation": []})


def test_default_invalid_step():
    with pytest.raises(AttributeError):
        default.init(jira_project_key="", steps={"new": ["unknown_step"]})


def test_unspecified_groups_come_from_default_steps():
    action = default.init(jira_project_key="", steps={"comment": ["create_comment"]})

    assert len(action.steps) == 3


def test_default_returns_callable_without_data():
    callable_object = default.init(jira_project_key="")
    assert callable_object
    with pytest.raises(TypeError) as exc_info:
        assert callable_object()

    assert "missing 1 required positional argument: 'context'" in str(exc_info.value)


def test_default_returns_callable_with_data(
    context_create_example: ActionContext, mocked_jira, mocked_bugzilla
):
    sentinel = mock.sentinel
    mocked_jira.create_issue.return_value = {"key": "k"}
    mocked_jira.create_or_update_issue_remote_links.return_value = sentinel
    mocked_bugzilla.get_bug.return_value = context_create_example.bug
    callable_object = default.init(jira_project_key=context_create_example.jira.project)

    handled, details = callable_object(context=context_create_example)

    assert handled
    assert details["responses"][0] == {"key": "k"}
    assert details["responses"][1] == sentinel
