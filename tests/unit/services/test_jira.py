import json
from unittest import mock

import pytest
import responses

from jbi.environment import get_settings
from jbi.services import jira


def test_counter_is_incremented_on_jira_create_issue():
    jira_client = jira.get_client()

    with mock.patch("jbi.services.common.statsd") as mocked:
        jira_client.create_issue({})

    mocked.incr.assert_called_with("jbi.jira.methods.create_issue.count")


def test_timer_is_used_on_jira_create_issue():
    jira_client = jira.get_client()

    with mock.patch("jbi.services.common.statsd") as mocked:
        jira_client.create_issue({})

    mocked.timer.assert_called_with("jbi.jira.methods.create_issue.timer")


@pytest.mark.no_mocked_jira
def test_create_issue_with_components(mocked_responses, context_create_example):
    url = f"{get_settings().jira_base_url}rest/api/2/project/{context_create_example.jira.project}/components"
    mocked_responses.add(
        responses.GET,
        url,
        json=[
            {
                "id": "10000",
                "name": "Component 1",
            },
            {
                "id": "42",
                "name": "Remote Settings",
            },
        ],
    )

    url = f"{get_settings().jira_base_url}rest/api/2/issue"
    mocked_responses.add(
        responses.POST,
        url,
        json={
            "id": "10000",
            "key": "ED-24",
        },
    )

    jira.create_jira_issue(
        context_create_example,
        "Description",
        sync_whiteboard_labels=False,
        components=["Remote Settings"],
    )

    posted_data = json.loads(mocked_responses.calls[-1].request.body)

    assert posted_data["fields"]["components"] == [{"id": "42"}]
