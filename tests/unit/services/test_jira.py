import json
import logging

import pytest
import requests
import responses
from requests.exceptions import ConnectionError

from jbi.environment import get_settings
from jbi.models import Actions, JiraComponents
from jbi.services import jira

pytestmark = pytest.mark.no_mocked_jira


def test_jira_create_issue_is_instrumented(
    mocked_responses, context_create_example, mocked_statsd
):
    url = f"{get_settings().jira_base_url}rest/api/2/issue"
    mocked_responses.add(
        responses.POST,
        url,
        json={
            "id": "10000",
            "key": "ED-24",
        },
    )

    jira.create_jira_issue(context_create_example, "Description", issue_type="Task")
    jira_client = jira.get_client()

    jira_client.create_issue({})

    mocked_statsd.incr.assert_called_with("jbi.jira.methods.create_issue.count")
    mocked_statsd.timer.assert_called_with("jbi.jira.methods.create_issue.timer")


def test_jira_calls_log_http_errors(mocked_responses, context_create_example, caplog):
    url = f"{get_settings().jira_base_url}rest/api/2/project/{context_create_example.jira.project}/components"
    mocked_responses.add(
        responses.GET,
        url,
        status=404,
        json={
            "errorMessages": ["No project could be found with key 'X'."],
            "errors": {},
        },
    )

    with caplog.at_level(logging.ERROR):
        with pytest.raises(requests.HTTPError):
            jira.get_client().get_project_components(
                context_create_example.jira.project
            )

    log_messages = [log.msg % log.args for log in caplog.records]
    idx = log_messages.index(
        "HTTP: GET /rest/api/2/project/JBI/components -> 404 Not Found"
    )
    log_record = caplog.records[idx]
    assert (
        log_record.body
        == '{"errorMessages": ["No project could be found with key \'X\'."], "errors": {}}'
    )


def test_jira_retries_failing_connections_in_health_check(
    mocked_responses, actions_factory
):
    url = f"{get_settings().jira_base_url}rest/api/2/serverInfo?doHealthCheck=True"

    # When the request does not match any mocked URL, we also obtain
    # a `ConnectionError`, but let's mock it explicitly.
    mocked_responses.add(
        responses.GET,
        url,
        body=ConnectionError(),
    )

    with pytest.raises(ConnectionError):
        jira.check_health(actions_factory())

    assert len(mocked_responses.calls) == 4


def test_jira_does_not_retry_4XX(mocked_responses, context_create_example):
    url = f"{get_settings().jira_base_url}rest/api/2/issue"
    mocked_responses.add(
        responses.POST,
        url,
        json={},
        status=400,
    )

    with pytest.raises(requests.HTTPError):
        jira.create_jira_issue(
            context=context_create_example, description="", issue_type="Task"
        )

    assert len(mocked_responses.calls) == 1


@pytest.mark.parametrize(
    "jira_components, project_components, expected_result",
    [
        (["Foo"], [{"name": "Foo"}], True),
        (["Foo"], [{"name": "Foo"}, {"name": "Bar"}], True),
        (["Foo", "Bar"], [{"name": "Foo"}, {"name": "Bar"}], True),
        ([], [], True),
        (["Foo"], [{"name": "Bar"}], False),
        (["Foo", "Bar"], [{"name": "Foo"}], False),
        (["Foo"], [], False),
    ],
)
def test_all_projects_components_exist(
    action_factory,
    jira_components,
    project_components,
    expected_result,
    mocked_responses,
):
    url = f"{get_settings().jira_base_url}rest/api/2/project/ABC/components"
    mocked_responses.add(
        responses.GET,
        url,
        json=project_components,
    )
    action = action_factory(
        parameters={
            "jira_project_key": "ABC",
            "jira_components": JiraComponents(set_custom_components=jira_components),
        }
    )
    actions = Actions(__root__=[action])
    result = jira._all_projects_components_exist(actions)
    assert result is expected_result


def test_all_projects_components_exist_no_components_param(
    action_factory, mocked_responses
):
    action = action_factory(
        parameters={
            "jira_project_key": "ABC",
        }
    )
    actions = Actions(__root__=[action])
    url = f"{get_settings().jira_base_url}rest/api/2/project/ABC/components"
    mocked_responses.add(
        responses.GET,
        url,
        json=[],
    )
    result = jira._all_projects_components_exist(actions)
    assert result is True
