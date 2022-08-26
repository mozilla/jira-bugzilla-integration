from unittest import mock

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
