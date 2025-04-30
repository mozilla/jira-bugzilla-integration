import logging

import pytest
import requests
import responses

from jbi.jira.client import JiraClient


@pytest.fixture
def jira_client(settings):
    return JiraClient(
        url=settings.jira_base_url,
        username=settings.jira_username,
        password=settings.jira_api_key,
        cloud=True,
    )


def test_jira_create_issue_is_instrumented(
    settings, jira_client, mocked_responses, context_create_example, mocked_statsd
):
    url = f"{settings.jira_base_url}rest/api/2/issue"
    mocked_responses.add(
        responses.POST,
        url,
        json={
            "id": "10000",
            "key": "ED-24",
        },
    )

    jira_client.create_issue({})

    mocked_statsd.incr.assert_called_with("jbi.jira.methods.create_issue.count")
    mocked_statsd.timer.assert_called_with("jbi.jira.methods.create_issue.timer")


def test_jira_calls_log_http_errors(
    settings, jira_client, mocked_responses, context_create_example, caplog
):
    url = f"{settings.jira_base_url}rest/api/2/project/{context_create_example.jira.project}/components"
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
            jira_client.get_project_components(context_create_example.jira.project)

    log_messages = [log.msg % log.args for log in caplog.records]
    idx = log_messages.index(
        "HTTP: GET /rest/api/2/project/JBI/components -> 404 Not Found"
    )
    log_record = caplog.records[idx]
    assert (
        log_record.body
        == '{"errorMessages": ["No project could be found with key \'X\'."], "errors": {}}'
    )


def test_paginated_projects_no_keys(settings, jira_client, mocked_responses):
    url = f"{settings.jira_base_url}rest/api/2/project/search"
    mocked_response_data = {"some": "data"}
    mocked_responses.add(
        responses.GET,
        url,
        status=200,
        match=[responses.matchers.query_string_matcher(None)],
        json=mocked_response_data,
    )
    resp = jira_client.paginated_projects()
    assert resp == mocked_response_data


def test_paginated_projects_with_keys(settings, jira_client, mocked_responses):
    url = f"{settings.jira_base_url}rest/api/2/project/search"
    mocked_response_data = {"some": "data"}
    mocked_responses.add(
        responses.GET,
        url,
        status=200,
        match=[responses.matchers.query_string_matcher("keys=['ABC', 'DEF']")],
        json=mocked_response_data,
    )
    resp = jira_client.paginated_projects(keys=["ABC", "DEF"])
    assert resp == mocked_response_data


def test_paginated_projects_greater_than_50_keys(
    settings, jira_client, mocked_responses
):
    keys = [str(i) for i in range(51)]
    with pytest.raises(ValueError):
        jira_client.paginated_projects(keys=keys)
