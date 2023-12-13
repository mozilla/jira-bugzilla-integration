import logging

import pytest
import requests
import responses
from requests.exceptions import ConnectionError

from jbi.environment import get_settings
from jbi.models import Actions, JiraComponents
from jbi.services import jira

pytestmark = pytest.mark.no_mocked_jira


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

    healthcheck = jira.get_service().check_health(actions_factory())
    assert healthcheck["up"] is False
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
def test_all_project_custom_components_exist(
    jira_components,
    project_components,
    expected_result,
    mocked_responses,
    action_factory,
):
    url = f"{get_settings().jira_base_url}rest/api/2/project/ABC/components"
    if jira_components:
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
    result = jira.get_service()._all_project_custom_components_exist(actions)
    assert result is expected_result


def test_all_project_custom_components_exist_no_components_param(
    action_factory, mocked_responses
):
    action = action_factory(
        parameters={
            "jira_project_key": "ABC",
        }
    )
    actions = Actions(root=[action])
    result = jira.get_service()._all_project_custom_components_exist(actions)
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

    before, after = capturelogs.records
    assert before.levelno == logging.DEBUG
    assert before.message == "Getting issue JBI-234"

    assert after.levelno == logging.ERROR
    assert after.message.startswith("Could not read issue JBI-234")


def test_get_issue_reraises_other_erroring_status_codes(
    mocked_responses, action_context_factory, capturelogs
):
    context = action_context_factory()
    url = f"{get_settings().jira_base_url}rest/api/2/issue/JBI-234"
    mocked_responses.add(responses.GET, url, status=401)

    with capturelogs.for_logger("jbi.services.jira").at_level(logging.DEBUG):
        with pytest.raises(requests.HTTPError):
            jira.get_service().get_issue(context=context, issue_key="JBI-234")

    [record] = capturelogs.records
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

    [message] = capturelogs.messages
    assert message == "Updating resolution of Jira issue JBI-234 to DONEZO"


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

    before, after = capturelogs.records
    assert before.message == f"Creating new Jira issue for Bug {context.bug.id}"
    assert before.fields == issue_fields

    assert after.message == f"Jira issue JBI-234 created for Bug {context.bug.id}"
    assert after.response == mocked_response_data


def test_create_jira_issue_when_list_is_returned(
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
    assert before.fields == issue_fields

    assert after.message == f"Jira issue JBI-234 created for Bug {context.bug.id}"
    assert after.levelno == logging.DEBUG
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

    assert str(exc.value) == "Failed to create issue for Bug 654321"

    before, after = capturelogs.records
    assert before.message == f"Creating new Jira issue for Bug {context.bug.id}"
    assert before.levelno == logging.DEBUG
    assert before.fields == issue_fields

    assert after.message == f"Failed to create issue for Bug {context.bug.id}"
    assert after.levelno == logging.ERROR
    assert after.response == fake_error_data


@pytest.mark.parametrize(
    "project_data, expected_result",
    [
        (
            [
                {"key": "ABC", "issueTypes": [{"name": "Task"}, {"name": "Bug"}]},
                {"key": "DEF", "issueTypes": [{"name": "Task"}, {"name": "Bug"}]},
            ],
            True,
        ),
        (
            [
                {"key": "ABC", "issueTypes": [{"name": "Task"}]},
                {"key": "DEF", "issueTypes": [{"name": "Task"}, {"name": "Bug"}]},
            ],
            False,
        ),
        (
            [
                {"key": "ABC", "issueTypes": [{"name": "Task"}, {"name": "Bug"}]},
            ],
            False,
        ),
    ],
)
def test_all_project_issue_types_exist(
    mocked_responses, action_factory, project_data, expected_result
):
    actions = Actions(
        root=[
            action_factory(whiteboard_tag="abc", parameters__jira_project_key="ABC"),
            action_factory(whiteboard_tag="def", parameters__jira_project_key="DEF"),
        ]
    )

    url = f"{get_settings().jira_base_url}rest/api/2/project/search"
    mocked_responses.add(
        responses.GET,
        url,
        status=200,
        match=[
            responses.matchers.query_string_matcher(
                "keys=ABC&keys=DEF&expand=issueTypes"
            )
        ],
        json={"values": project_data},
    )

    assert jira.get_service()._all_project_issue_types_exist(actions) == expected_result


def test_visible_projects(mocked_responses):
    url = f"{get_settings().jira_base_url}rest/api/2/permissions/project"
    mocked_responses.add(
        responses.POST,
        url,
        status=200,
        match=[
            responses.matchers.json_params_matcher(
                {"permissions": []},
            )
        ],
        json={"projects": [{"key": "ABC"}, {"key": "DEF"}]},
    )

    projects = jira.get_service().fetch_visible_projects()
    assert projects == ["ABC", "DEF"]


@pytest.mark.parametrize(
    "project_data, expected_result",
    [
        (
            [{"key": "ABC"}, {"key": "DEF"}],
            True,
        ),
        (
            [{"key": "ABC"}],
            False,
        ),
    ],
)
def test_all_projects_permissions(
    mocked_responses, action_factory, project_data, expected_result
):
    actions = Actions(
        root=[
            action_factory(whiteboard_tag="abc", parameters__jira_project_key="ABC"),
            action_factory(whiteboard_tag="def", parameters__jira_project_key="DEF"),
        ]
    )

    url = f"{get_settings().jira_base_url}rest/api/2/permissions/project"
    mocked_responses.add(
        responses.POST,
        url,
        status=200,
        match=[
            responses.matchers.json_params_matcher(
                {"permissions": list(jira.JIRA_REQUIRED_PERMISSIONS)},
            )
        ],
        json={"projects": project_data},
    )

    assert jira.get_service()._all_projects_permissions(actions) == expected_result
