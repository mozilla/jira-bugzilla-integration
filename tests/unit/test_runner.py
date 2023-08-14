import logging
from unittest import mock

import pytest
import requests
import responses

from jbi import Operation
from jbi.environment import get_settings
from jbi.errors import IgnoreInvalidRequestError
from jbi.models import ActionContext, BugzillaWebhookRequest
from jbi.runner import Executor, execute_action


@pytest.fixture
def webhook_comment_example(webhook_factory) -> BugzillaWebhookRequest:
    return webhook_factory(
        bug__see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
        bug__comment__number=2,
        bug__comment__body="hello",
        event__target="comment",
        event__user__login="mathieu@mozilla.org",
    )


def test_bugzilla_object_is_always_fetched(
    mocked_jira, mocked_bugzilla, webhook_create_example, actions_factory, bug_factory
):
    # See https://github.com/mozilla/jira-bugzilla-integration/issues/292
    fetched_bug = bug_factory(
        id=webhook_create_example.bug.id,
        see_also=[f"{get_settings().jira_base_url}browse/JBI-234"],
    )
    mocked_bugzilla.get_bug.return_value = fetched_bug
    mocked_jira.get_issue.return_value = {"fields": {"project": {"key": "JBI"}}}

    execute_action(
        request=webhook_create_example,
        actions=actions_factory(),
    )

    mocked_bugzilla.get_bug.assert_called_once_with(webhook_create_example.bug.id)


def test_request_is_ignored_because_project_mismatch(
    webhook_create_example: BugzillaWebhookRequest,
    actions_factory,
    mocked_jira,
    mocked_bugzilla,
    bug_factory,
):
    bug = bug_factory(
        id=webhook_create_example.bug.id,
        see_also=[f"{get_settings().jira_base_url}browse/JBI-234"],
    )
    webhook_create_example.bug = bug
    mocked_bugzilla.get_bug.return_value = bug
    mocked_jira.get_issue.return_value = {"fields": {"project": {"key": "FXDROID"}}}

    with pytest.raises(IgnoreInvalidRequestError) as exc_info:
        execute_action(
            request=webhook_create_example,
            actions=actions_factory(),
        )

    assert str(exc_info.value) == "ignore linked project 'FXDROID' (!='JBI')"


def test_request_is_ignored_because_private(
    webhook_create_example: BugzillaWebhookRequest,
    actions_factory,
    mocked_bugzilla,
    bug_factory,
):
    bug = bug_factory(id=webhook_create_example.bug.id, is_private=True)
    webhook_create_example.bug = bug
    mocked_bugzilla.get_bug.return_value = bug

    with pytest.raises(IgnoreInvalidRequestError) as exc_info:
        execute_action(
            request=webhook_create_example,
            actions=actions_factory(),
        )

    assert str(exc_info.value) == "private bugs are not supported"


def test_added_comment_without_linked_issue_is_ignored(
    webhook_comment_example: BugzillaWebhookRequest,
    actions_factory,
    mocked_bugzilla,
):
    webhook_comment_example.bug.see_also = []
    mocked_bugzilla.get_bug.return_value = webhook_comment_example.bug

    with pytest.raises(IgnoreInvalidRequestError) as exc_info:
        execute_action(
            request=webhook_comment_example,
            actions=actions_factory(),
        )
    assert str(exc_info.value) == "ignore event target 'comment'"


def test_request_is_ignored_because_no_action(
    webhook_create_example: BugzillaWebhookRequest,
    actions_factory,
    mocked_bugzilla,
):
    assert webhook_create_example.bug
    webhook_create_example.bug.whiteboard = "bar"
    mocked_bugzilla.get_bug.return_value = webhook_create_example.bug

    with pytest.raises(IgnoreInvalidRequestError) as exc_info:
        execute_action(
            request=webhook_create_example,
            actions=actions_factory(),
        )
    assert str(exc_info.value) == "no bug whiteboard matching action tags: devtest"


def test_execution_logging_for_successful_requests(
    capturelogs,
    webhook_create_example: BugzillaWebhookRequest,
    actions_factory,
    mocked_bugzilla,
):
    mocked_bugzilla.get_bug.return_value = webhook_create_example.bug

    with capturelogs.for_logger("jbi.runner").at_level(logging.DEBUG):
        execute_action(
            request=webhook_create_example,
            actions=actions_factory(),
        )

    assert {
        "Handling incoming request",
        "Execute action 'devtest' for Bug 654321",
        "Action 'devtest' executed successfully for Bug 654321",
    }.issubset(set(capturelogs.messages))


def test_execution_logging_for_ignored_requests(
    capturelogs,
    webhook_create_example: BugzillaWebhookRequest,
    actions_factory,
    mocked_bugzilla,
):
    assert webhook_create_example.bug
    webhook_create_example.bug.whiteboard = "foo"
    mocked_bugzilla.get_bug.return_value = webhook_create_example.bug

    with capturelogs.for_logger("jbi.runner").at_level(logging.DEBUG):
        with pytest.raises(IgnoreInvalidRequestError):
            execute_action(
                request=webhook_create_example,
                actions=actions_factory(),
            )

    assert capturelogs.messages == [
        "Handling incoming request",
        "Ignore incoming request: no bug whiteboard matching action tags: devtest",
    ]


def test_action_is_logged_as_success_if_returns_true(
    capturelogs,
    webhook_create_example: BugzillaWebhookRequest,
    actions_factory,
    mocked_bugzilla,
):
    mocked_bugzilla.get_bug.return_value = webhook_create_example.bug

    with mock.patch("jbi.runner.Executor.__call__", return_value=(True, {})):
        with capturelogs.for_logger("jbi.runner").at_level(logging.DEBUG):
            execute_action(
                request=webhook_create_example,
                actions=actions_factory(),
            )

    captured_log_msgs = [(r.getMessage(), r.operation) for r in capturelogs.records]

    assert captured_log_msgs == [
        ("Handling incoming request", Operation.HANDLE),
        (
            "Execute action 'devtest' for Bug 654321",
            Operation.EXECUTE,
        ),
        ("Action 'devtest' executed successfully for Bug 654321", Operation.SUCCESS),
    ]
    assert capturelogs.records[-1].bug["id"] == 654321
    assert capturelogs.records[-1].action["whiteboard_tag"] == "devtest"


def test_action_is_logged_as_ignore_if_returns_false(
    capturelogs,
    webhook_create_example: BugzillaWebhookRequest,
    actions_factory,
    mocked_bugzilla,
):
    mocked_bugzilla.get_bug.return_value = webhook_create_example.bug

    with mock.patch("jbi.runner.Executor.__call__", return_value=(False, {})):
        with capturelogs.for_logger("jbi.runner").at_level(logging.DEBUG):
            execute_action(
                request=webhook_create_example,
                actions=actions_factory(),
            )

    captured_log_msgs = [(r.getMessage(), r.operation) for r in capturelogs.records]

    assert captured_log_msgs == [
        ("Handling incoming request", Operation.HANDLE),
        (
            "Execute action 'devtest' for Bug 654321",
            Operation.EXECUTE,
        ),
        ("Action 'devtest' executed successfully for Bug 654321", Operation.IGNORE),
    ]


def test_counter_is_incremented_on_ignored_requests(
    webhook_create_example: BugzillaWebhookRequest,
    actions_factory,
    mocked_bugzilla,
):
    assert webhook_create_example.bug
    webhook_create_example.bug.whiteboard = "foo"
    mocked_bugzilla.get_bug.return_value = webhook_create_example.bug

    with mock.patch("jbi.runner.statsd") as mocked:
        with pytest.raises(IgnoreInvalidRequestError):
            execute_action(
                request=webhook_create_example,
                actions=actions_factory(),
            )
    mocked.incr.assert_called_with("jbi.bugzilla.ignored.count")


def test_counter_is_incremented_on_processed_requests(
    webhook_create_example: BugzillaWebhookRequest,
    actions_factory,
    mocked_bugzilla,
):
    mocked_bugzilla.get_bug.return_value = webhook_create_example.bug

    with mock.patch("jbi.runner.statsd") as mocked:
        execute_action(
            request=webhook_create_example,
            actions=actions_factory(),
        )
    mocked.incr.assert_called_with("jbi.bugzilla.processed.count")


def test_runner_ignores_if_jira_issue_is_not_readable(
    webhook_comment_example: BugzillaWebhookRequest,
    actions_factory,
    mocked_bugzilla,
    mocked_jira,
    capturelogs,
):
    mocked_jira.get_issue.return_value = None
    mocked_bugzilla.get_bug.return_value = webhook_comment_example.bug

    with capturelogs.for_logger("jbi.runner").at_level(logging.DEBUG):
        with pytest.raises(IgnoreInvalidRequestError) as exc_info:
            execute_action(
                request=webhook_comment_example,
                actions=actions_factory(),
            )

    assert str(exc_info.value) == "ignore unreadable issue JBI-234"
    assert capturelogs.messages == [
        "Handling incoming request",
        "Ignore incoming request: ignore unreadable issue JBI-234",
    ]


def test_runner_ignores_request_if_jira_is_linked_but_without_whiteboard(
    webhook_comment_example: BugzillaWebhookRequest,
    actions_factory,
    mocked_bugzilla,
):
    mocked_bugzilla.get_bug.return_value = webhook_comment_example.bug
    webhook_comment_example.bug.whiteboard = "[not-matching-local-config]"

    assert (
        webhook_comment_example.bug.extract_from_see_also(project_key="foo") is not None
    )

    with pytest.raises(IgnoreInvalidRequestError) as exc_info:
        execute_action(
            request=webhook_comment_example,
            actions=actions_factory(),
        )

    assert str(exc_info.value) == "no bug whiteboard matching action tags: devtest"


def test_default_invalid_init():
    with pytest.raises(TypeError):
        Executor()


def test_unspecified_groups_come_from_default_steps(action_params_factory):
    action = Executor(action_params_factory(steps={"comment": ["create_comment"]}))

    assert len(action.steps) == 3
    assert action.steps


def test_default_returns_callable_without_data(action_params_factory):
    callable_object = Executor(action_params_factory())
    assert callable_object
    with pytest.raises(TypeError) as exc_info:
        assert callable_object()

    assert "missing 1 required positional argument: 'context'" in str(exc_info.value)


@pytest.mark.no_mocked_bugzilla
@pytest.mark.no_mocked_jira
def test_default_logs_all_received_responses(
    mocked_responses,
    capturelogs,
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

    action = Executor(
        action_params_factory(
            steps={"new": [], "existing": [], "comment": ["create_comment"]}
        )
    )

    with capturelogs.for_logger("jbi.runner").at_level(logging.DEBUG):
        action(context=context_comment_example)

    captured_log_msgs = (
        (r.msg % r.args, r.response)
        for r in capturelogs.records
        if r.name == "jbi.runner"
    )

    assert (
        "Received {'id': '10000', 'key': 'ED-24'}",
        {"id": "10000", "key": "ED-24"},
    ) in captured_log_msgs


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
    callable_object = Executor(
        action_params_factory(jira_project_key=context_create_example.jira.project)
    )

    handled, details = callable_object(context=context_create_example)

    assert handled
    assert details["responses"][0] == {"key": "k"}
    assert details["responses"][1] == sentinel


def test_counter_is_incremented_when_workflows_was_aborted(
    mocked_bugzilla,
    mocked_jira,
    action_context_factory,
    action_factory,
    action_params_factory,
):
    context_create_example: ActionContext = action_context_factory(
        operation=Operation.CREATE,
        action=action_factory(whiteboard_tag="fnx"),
    )
    mocked_bugzilla.get_bug.return_value = context_create_example.bug
    mocked_jira.create_or_update_issue_remote_links.side_effect = requests.HTTPError(
        "Unauthorized"
    )
    callable_object = Executor(
        action_params_factory(jira_project_key=context_create_example.jira.project)
    )

    with mock.patch("jbi.runner.statsd") as mocked:
        with pytest.raises(requests.HTTPError):
            callable_object(context=context_create_example)

    mocked.incr.assert_called_with("jbi.action.fnx.aborted.count")


def test_counter_is_incremented_when_workflows_was_incomplete(
    mocked_bugzilla,
    action_context_factory,
    action_factory,
    bug_factory,
    action_params_factory,
):
    context_create_example: ActionContext = action_context_factory(
        operation=Operation.CREATE,
        action=action_factory(whiteboard_tag="fnx"),
        bug=bug_factory(resolution="WONTFIX"),
    )
    mocked_bugzilla.get_bug.return_value = context_create_example.bug
    callable_object = Executor(
        action_params_factory(
            jira_project_key=context_create_example.jira.project,
            steps={
                "new": [
                    "create_issue",
                    "maybe_update_issue_resolution",
                ]
            },
            resolution_map={
                # Not matching WONTFIX, `maybe_` step will not complete
                "DUPLICATE": "Duplicate",
            },
        )
    )

    with mock.patch("jbi.runner.statsd") as mocked:
        callable_object(context=context_create_example)

    mocked.incr.assert_called_with("jbi.action.fnx.incomplete.count")
