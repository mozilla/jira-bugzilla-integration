import logging
from unittest import mock

import pytest
import responses

from jbi.actions import default
from jbi.environment import get_settings
from jbi.models import ActionContext
from tests.conftest import action_params_factory


def test_default_invalid_init():
    with pytest.raises(TypeError):
        default.init()


def test_default_invalid_operation(action_params_factory):
    with pytest.raises(ValueError):
        default.init(action_params_factory(steps={"bad-operation": []}))


def test_default_invalid_step(action_params_factory):
    with pytest.raises(AttributeError):
        default.init(action_params_factory(steps={"new": ["unknown_step"]}))


def test_unspecified_groups_come_from_default_steps(action_params_factory):
    action = default.init(action_params_factory(steps={"comment": ["create_comment"]}))

    assert len(action.steps) == 3


def test_default_returns_callable_without_data(action_params_factory):
    callable_object = default.init(action_params_factory())
    assert callable_object
    with pytest.raises(TypeError) as exc_info:
        assert callable_object()

    assert "missing 1 required positional argument: 'context'" in str(exc_info.value)


@pytest.mark.no_mocked_bugzilla
@pytest.mark.no_mocked_jira
def test_default_logs_all_received_responses(
    mocked_responses,
    caplog,
    context_comment_example: ActionContext,
    action_params_factory,
):
    # In this test, we don't mock the Jira and Bugzilla clients
    # because we want to make sure that actual responses objects are logged
    # successfully.
    settings = get_settings()
    url = f"{settings.jira_base_url}rest/api/2/issue/JBI-234/comment"
    mocked_responses.add(
        responses.POST,
        url,
        json={
            "id": "10000",
            "key": "ED-24",
        },
    )

    action = default.init(
        action_params_factory(
            steps={"new": [], "existing": [], "comment": ["create_comment"]}
        )
    )

    with caplog.at_level(logging.DEBUG):
        action(context=context_comment_example)

    captured_log_msgs = [
        (r.msg % r.args, r.response)
        for r in caplog.records
        if r.name == "jbi.actions.default"
    ]

    assert captured_log_msgs == [
        (
            "Received {'id': '10000', 'key': 'ED-24'}",
            {"id": "10000", "key": "ED-24"},
        )
    ]


def test_default_returns_callable_with_data(
    context_create_example: ActionContext,
    mocked_jira,
    mocked_bugzilla,
    action_params_factory,
):
    sentinel = mock.sentinel
    mocked_jira.create_issue.return_value = {"key": "k"}
    mocked_jira.create_or_update_issue_remote_links.return_value = sentinel
    mocked_bugzilla.get_bug.return_value = context_create_example.bug
    mocked_bugzilla.get_comments.return_value = []
    callable_object = default.init(
        action_params_factory(jira_project_key=context_create_example.jira.project)
    )

    handled, details = callable_object(context=context_create_example)

    assert handled
    assert details["responses"][0] == {"key": "k"}
    assert details["responses"][1] == sentinel
