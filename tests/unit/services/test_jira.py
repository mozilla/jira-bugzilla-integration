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

    jira.get_service().create_jira_issue(
        context_create_example, "Description", issue_type="Task"
    )
    jira_client = jira.get_service().client

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
            jira.get_service().client.get_project_components(
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
        jira.get_service().check_health(actions_factory())

    assert len(mocked_responses.calls) == 4


def test_jira_does_not_retry_4XX(mocked_responses, context_create_example):
    url = f"{get_settings().jira_base_url}rest/api/2/issue"
    mocked_responses.add(
        responses.POST,
        url,
        json={"errorMessages": ["You done goofed"]},
        status=400,
    )

    with pytest.raises(jira.JiraCreateError):
        jira.get_service().create_jira_issue(
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
    actions = Actions(root=[action])
    result = jira.get_service()._all_projects_components_exist(actions)
    assert result is expected_result


def test_all_projects_components_exist_no_components_param(
    action_factory, mocked_responses
):
    action = action_factory(
        parameters={
            "jira_project_key": "ABC",
        }
    )
    actions = Actions(root=[action])
    url = f"{get_settings().jira_base_url}rest/api/2/project/ABC/components"
    mocked_responses.add(
        responses.GET,
        url,
        json=[],
    )
    result = jira.get_service()._all_projects_components_exist(actions)
    assert result is True


def test_get_issue(mocked_responses, action_context_factory, capturelogs):
    context = action_context_factory()
    url = f"{get_settings().jira_base_url}rest/api/2/issue/JBI-234"
    mock_response_data = {"key": "JBI-234", "fields": {"project": {"key": "JBI"}}}
    mocked_responses.add(responses.GET, url, json=mock_response_data)

    with capturelogs.for_logger("jbi.services.jira").at_level(logging.DEBUG):
        response = jira.get_service().get_issue(context=context, issue_key="JBI-234")

    assert response == mock_response_data
    for record in capturelogs.records:
        assert record.rid == context.rid
        assert record.action["whiteboard_tag"] == context.action.whiteboard_tag

    before, after = capturelogs.messages
    assert before == "Getting issue JBI-234"
    assert after == "Received issue JBI-234"


def test_get_issue_handles_404(mocked_responses, action_context_factory, capturelogs):
    context = action_context_factory()
    url = f"{get_settings().jira_base_url}rest/api/2/issue/JBI-234"
    mocked_responses.add(responses.GET, url, status=404)

    with capturelogs.for_logger("jbi.services.jira").at_level(logging.DEBUG):
        return_val = jira.get_service().get_issue(context=context, issue_key="JBI-234")

    assert return_val is None

    for record in capturelogs.records:
        assert record.rid == context.rid

    before, after = capturelogs.records
    assert before.levelno == logging.DEBUG
    assert before.message == "Getting issue JBI-234"

    assert after.levelno == logging.ERROR
    assert after.message.startswith("Could not read issue JBI-234")


def test_get_issue_raises_other_error(
    mocked_responses, action_context_factory, capturelogs
):
    context = action_context_factory()
    url = f"{get_settings().jira_base_url}rest/api/2/issue/JBI-234"
    mocked_responses.add(responses.GET, url, status=401)

    with capturelogs.for_logger("jbi.services.jira").at_level(logging.DEBUG):
        with pytest.raises(requests.HTTPError):
            jira.get_service().get_issue(context=context, issue_key="JBI-234")

    [record] = capturelogs.records
    assert record.rid == context.rid
    assert record.levelno == logging.DEBUG
    assert record.message == "Getting issue JBI-234"


def test_update_issue_resolution(mocked_responses, action_context_factory, capturelogs):
    context = action_context_factory(jira__issue="JBI-234")
    url = f"{get_settings().jira_base_url}rest/api/2/issue/JBI-234"
    mocked_responses.add(
        responses.PUT,
        url,
        match=[
            responses.matchers.json_params_matcher({"fields": {"resolution": "DONEZO"}})
        ],
    )

    with capturelogs.for_logger("jbi.services.jira").at_level(logging.DEBUG):
        jira.get_service().update_issue_resolution(
            context=context, jira_resolution="DONEZO"
        )

    for record in capturelogs.records:
        assert record.rid == context.rid
        assert record.levelno == logging.DEBUG

    before, after = capturelogs.messages
    assert before == "Updating resolution of Jira issue JBI-234 to DONEZO"
    assert after == "Updated resolution of Jira issue JBI-234 to DONEZO"


def test_update_issue_resolution_raises(
    mocked_responses, action_context_factory, capturelogs
):
    context = action_context_factory(jira__issue="JBI-234")
    url = f"{get_settings().jira_base_url}rest/api/2/issue/JBI-234"
    mocked_responses.add(
        responses.PUT,
        url,
        status=401,
        match=[
            responses.matchers.json_params_matcher({"fields": {"resolution": "DONEZO"}})
        ],
    )

    with capturelogs.for_logger("jbi.services.jira").at_level(logging.DEBUG):
        with pytest.raises(requests.HTTPError):
            jira.get_service().update_issue_resolution(
                context=context, jira_resolution="DONEZO"
            )

    [record] = capturelogs.records
    assert record.rid == context.rid
    assert record.levelno == logging.DEBUG
    assert record.message == "Updating resolution of Jira issue JBI-234 to DONEZO"


def test_create_jira_issue(mocked_responses, action_context_factory, capturelogs):
    context = action_context_factory(jira__project_key="JBI")
    url = f"{get_settings().jira_base_url}rest/api/2/issue"
    mocked_response_data = {"key": "JBI-234"}
    issue_fields = {
        "summary": context.bug.summary,
        "issuetype": {"name": "Task"},
        "description": "description",
        "project": {"key": "JBI"},
    }
    mocked_responses.add(
        responses.POST,
        url,
        status=201,
        match=[
            responses.matchers.json_params_matcher(
                {"fields": issue_fields},
            )
        ],
        json=mocked_response_data,
    )

    with capturelogs.for_logger("jbi.services.jira").at_level(logging.DEBUG):
        response = jira.get_service().create_jira_issue(
            context=context, description="description", issue_type="Task"
        )

    assert response == mocked_response_data

    for record in capturelogs.records:
        assert record.rid == context.rid
        assert record.levelno == logging.DEBUG

    before, after = capturelogs.records
    assert before.message == f"Creating new Jira issue for Bug {context.bug.id}"
    assert before.fields == issue_fields

    assert after.message == f"Jira issue JBI-234 created for Bug {context.bug.id}"
    assert after.response == mocked_response_data


def test_create_jira_issue_returns_list(
    mocked_responses, action_context_factory, capturelogs
):
    context = action_context_factory(jira__project_key="JBI")
    url = f"{get_settings().jira_base_url}rest/api/2/issue"
    mocked_issue_data = {"key": "JBI-234"}
    issue_fields = {
        "summary": context.bug.summary,
        "issuetype": {"name": "Task"},
        "description": "description",
        "project": {"key": "JBI"},
    }
    mocked_responses.add(
        responses.POST,
        url,
        status=201,
        match=[
            responses.matchers.json_params_matcher(
                {"fields": issue_fields},
            )
        ],
        json=[mocked_issue_data],
    )

    with capturelogs.for_logger("jbi.services.jira").at_level(logging.DEBUG):
        issue_data = jira.get_service().create_jira_issue(
            context=context, description="description", issue_type="Task"
        )

    assert issue_data == mocked_issue_data

    before, after = capturelogs.records
    assert before.message == f"Creating new Jira issue for Bug {context.bug.id}"
    assert before.levelno == logging.DEBUG
    assert before.rid == context.rid
    assert before.fields == issue_fields

    assert after.message == f"Jira issue JBI-234 created for Bug {context.bug.id}"
    assert after.levelno == logging.DEBUG
    assert after.rid == context.rid
    assert after.response == [mocked_issue_data]


def test_create_jira_issue_returns_errors(
    mocked_responses, action_context_factory, capturelogs
):
    context = action_context_factory(jira__project_key="JBI")
    url = f"{get_settings().jira_base_url}rest/api/2/issue"
    fake_error_data = {
        "errorMessages": ["You done goofed"],
        "errors": ["You messed up this time"],
    }
    issue_fields = {
        "summary": context.bug.summary,
        "issuetype": {"name": "Task"},
        "description": "description",
        "project": {"key": "JBI"},
    }
    mocked_responses.add(responses.POST, url, status=400, json=fake_error_data)

    with capturelogs.for_logger("jbi.services.jira").at_level(logging.DEBUG):
        with pytest.raises(jira.JiraCreateError) as exc:
            jira.get_service().create_jira_issue(
                context=context, description="description", issue_type="Task"
            )
        assert str(exc.value) == "You done goofed, You messed up this time"

    before, after = capturelogs.records
    assert before.message == f"Creating new Jira issue for Bug {context.bug.id}"
    assert before.rid == context.rid
    assert before.levelno == logging.DEBUG
    assert before.fields == issue_fields

    assert after.message == f"Failed to create issue for Bug {context.bug.id}"
    assert after.rid == context.rid
    assert after.levelno == logging.ERROR
    assert after.response == fake_error_data
