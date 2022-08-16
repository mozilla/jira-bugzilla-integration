from unittest import mock

from jbi.services.jira import get_jira


def test_counter_is_incremented_on_jira_create_issue():
    jira_client = get_jira()

    with mock.patch("jbi.services.common.statsd") as mocked:
        jira_client.create_issue({})

    mocked.incr.assert_called_with("jbi.jira.methods.create_issue.count")


def test_timer_is_used_on_jira_create_issue():
    jira_client = get_jira()

    with mock.patch("jbi.services.common.statsd") as mocked:
        jira_client.create_issue({})

    mocked.timer.assert_called_with("jbi.jira.methods.create_issue.timer")
