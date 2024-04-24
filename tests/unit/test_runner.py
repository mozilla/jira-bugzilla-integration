import logging
from unittest import mock

import pytest
import requests
import responses

from jbi import Operation
from jbi.environment import get_settings
from jbi.errors import ActionNotFoundError, IgnoreInvalidRequestError
from jbi.models import ActionContext
from jbi.runner import (
    Actions,
    Executor,
    execute_action,
    execute_or_queue,
    lookup_action,
)


def test_bugzilla_object_is_always_fetched(
    mocked_jira, mocked_bugzilla, bugzilla_webhook_request, actions, bug_factory
):
    # See https://github.com/mozilla/jira-bugzilla-integration/issues/292
    fetched_bug = bug_factory(
        id=bugzilla_webhook_request.bug.id,
        see_also=[f"{get_settings().jira_base_url}browse/JBI-234"],
    )
    mocked_bugzilla.get_bug.return_value = fetched_bug
    mocked_jira.get_issue.return_value = {"fields": {"project": {"key": "JBI"}}}

    execute_action(request=bugzilla_webhook_request, actions=actions)

    mocked_bugzilla.get_bug.assert_called_once_with(bugzilla_webhook_request.bug.id)


def test_request_is_ignored_because_project_mismatch(
    webhook_request_factory,
    actions,
    mocked_jira,
    mocked_bugzilla,
    bug_factory,
    settings,
):
    webhook = webhook_request_factory(
        bug__see_also=[f"{settings.jira_base_url}browse/JBI-234"]
    )
    mocked_bugzilla.get_bug.return_value = webhook.bug
    mocked_jira.get_issue.return_value = {"fields": {"project": {"key": "FXDROID"}}}

    with pytest.raises(IgnoreInvalidRequestError) as exc_info:
        execute_action(request=webhook, actions=actions)

    assert str(exc_info.value) == "ignore linked project 'FXDROID' (!='JBI')"


def test_request_is_ignored_because_private(
    webhook_request_factory,
    actions,
    mocked_bugzilla,
    bug_factory,
):
    webhook = webhook_request_factory(bug__is_private=True)
    mocked_bugzilla.get_bug.return_value = webhook.bug

    with pytest.raises(IgnoreInvalidRequestError) as exc_info:
        execute_action(request=webhook, actions=actions)

    assert str(exc_info.value) == "private bugs are not supported"


def test_added_comment_without_linked_issue_is_ignored(
    actions, mocked_bugzilla, webhook_request_factory
):
    webhook_with_comment = webhook_request_factory(
        bug__see_also=[],
        bug__comment__number=2,
        bug__comment__body="hello",
        event__target="comment",
        event__user__login="mathieu@mozilla.org",
    )
    mocked_bugzilla.get_bug.return_value = webhook_with_comment.bug

    with pytest.raises(IgnoreInvalidRequestError) as exc_info:
        execute_action(request=webhook_with_comment, actions=actions)
    assert str(exc_info.value) == "ignore event target 'comment'"


def test_request_is_ignored_because_no_action(
    webhook_request_factory,
    actions,
    mocked_bugzilla,
):
    webhook = webhook_request_factory(bug__whiteboard="bar")
    mocked_bugzilla.get_bug.return_value = webhook.bug

    with pytest.raises(IgnoreInvalidRequestError) as exc_info:
        execute_action(request=webhook, actions=actions)
    assert str(exc_info.value) == "no bug whiteboard matching action tags: devtest"


def test_execution_logging_for_successful_requests(
    capturelogs,
    bugzilla_webhook_request,
    actions,
    mocked_bugzilla,
):
    mocked_bugzilla.get_bug.return_value = bugzilla_webhook_request.bug

    with capturelogs.for_logger("jbi.runner").at_level(logging.DEBUG):
        execute_action(request=bugzilla_webhook_request, actions=actions)

    assert {
        "Handling incoming request",
        "Execute action 'devtest' for Bug 654321",
        "Action 'devtest' executed successfully for Bug 654321",
    }.issubset(set(capturelogs.messages))


def test_execution_logging_for_ignored_requests(
    capturelogs,
    webhook_request_factory,
    actions,
    mocked_bugzilla,
):
    webhook = webhook_request_factory(bug__whiteboard="foo")
    mocked_bugzilla.get_bug.return_value = webhook.bug

    with capturelogs.for_logger("jbi.runner").at_level(logging.DEBUG):
        with pytest.raises(IgnoreInvalidRequestError):
            execute_action(request=webhook, actions=actions)

    assert capturelogs.messages == [
        "Handling incoming request",
        "Ignore incoming request: no bug whiteboard matching action tags: devtest",
    ]


def test_action_is_logged_as_success_if_returns_true(
    capturelogs,
    bugzilla_webhook_request,
    actions,
    mocked_bugzilla,
):
    mocked_bugzilla.get_bug.return_value = bugzilla_webhook_request.bug

    with mock.patch("jbi.runner.Executor.__call__", return_value=(True, {})):
        with capturelogs.for_logger("jbi.runner").at_level(logging.DEBUG):
            execute_action(request=bugzilla_webhook_request, actions=actions)

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
    bugzilla_webhook_request,
    actions,
    mocked_bugzilla,
):
    mocked_bugzilla.get_bug.return_value = bugzilla_webhook_request.bug

    with mock.patch("jbi.runner.Executor.__call__", return_value=(False, {})):
        with capturelogs.for_logger("jbi.runner").at_level(logging.DEBUG):
            execute_action(request=bugzilla_webhook_request, actions=actions)

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
    webhook_request_factory,
    actions,
    mocked_bugzilla,
):
    webhoook = webhook_request_factory(bug__whiteboard="foo")
    mocked_bugzilla.get_bug.return_value = webhoook.bug

    with mock.patch("jbi.runner.statsd") as mocked:
        with pytest.raises(IgnoreInvalidRequestError):
            execute_action(request=webhoook, actions=actions)
    mocked.incr.assert_called_with("jbi.bugzilla.ignored.count")


def test_counter_is_incremented_on_processed_requests(
    bugzilla_webhook_request,
    actions,
    mocked_bugzilla,
):
    mocked_bugzilla.get_bug.return_value = bugzilla_webhook_request.bug

    with mock.patch("jbi.runner.statsd") as mocked:
        execute_action(request=bugzilla_webhook_request, actions=actions)
    mocked.incr.assert_called_with("jbi.bugzilla.processed.count")


def test_runner_ignores_if_jira_issue_is_not_readable(
    actions,
    webhook_request_factory,
    mocked_bugzilla,
    mocked_jira,
    capturelogs,
):
    webhook = webhook_request_factory(
        bug__see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
    )
    mocked_jira.get_issue.return_value = None
    mocked_bugzilla.get_bug.return_value = webhook.bug

    with capturelogs.for_logger("jbi.runner").at_level(logging.DEBUG):
        with pytest.raises(IgnoreInvalidRequestError) as exc_info:
            execute_action(request=webhook, actions=actions)

    assert str(exc_info.value) == "ignore unreadable issue JBI-234"
    assert capturelogs.messages == [
        "Handling incoming request",
        "Ignore incoming request: ignore unreadable issue JBI-234",
    ]


def test_runner_ignores_request_if_jira_is_linked_but_without_whiteboard(
    webhook_request_factory,
    actions,
    mocked_bugzilla,
):
    webhook = webhook_request_factory(
        bug__see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
        bug__whiteboard="[not-matching-local-config]",
    )
    mocked_bugzilla.get_bug.return_value = webhook.bug

    assert webhook.bug.extract_from_see_also(project_key="foo") is not None

    with pytest.raises(IgnoreInvalidRequestError) as exc_info:
        execute_action(request=webhook, actions=actions)

    assert str(exc_info.value) == "no bug whiteboard matching action tags: devtest"


@pytest.mark.asyncio
async def test_execute_or_queue_happy_path(
    mock_queue,
    bugzilla_webhook_request,
):
    mock_queue.is_blocked.return_value = False
    await execute_or_queue(
        request=bugzilla_webhook_request,
        queue=mock_queue,
        actions=mock.MagicMock(spec=Actions),
    )
    mock_queue.is_blocked.assert_called_once()
    mock_queue.postpone.assert_not_called()
    mock_queue.track_failed.assert_not_called()


@pytest.mark.asyncio
async def test_execute_or_queue_blocked(
    actions,
    mock_queue,
    bugzilla_webhook_request,
):
    mock_queue.is_blocked.return_value = True
    await execute_or_queue(
        request=bugzilla_webhook_request,
        queue=mock_queue,
        actions=mock.MagicMock(spec=Actions),
    )
    mock_queue.is_blocked.assert_called_once()
    mock_queue.postpone.assert_called_once()
    mock_queue.track_failed.assert_not_called()


@pytest.mark.asyncio
async def test_execute_or_queue_exception(
    actions,
    mock_queue,
    bugzilla_webhook_request,
):
    mock_queue.is_blocked.return_value = False
    # should trigger an exception for this scenario
    await execute_or_queue(
        request=bugzilla_webhook_request, queue=mock_queue, actions=actions
    )
    mock_queue.is_blocked.assert_called_once()
    mock_queue.postpone.assert_not_called()
    mock_queue.track_failed.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.no_mocked_bugzilla
@pytest.mark.no_mocked_jira
async def test_execute_or_queue_http_error_details(
    actions,
    dl_queue,
    bugzilla_webhook_request,
    context_comment_example,
    mocked_responses,
):
    bug = bugzilla_webhook_request.bug
    settings = get_settings()
    mocked_responses.add(
        responses.GET,
        f"{settings.bugzilla_base_url}/rest/bug/{bug.id}",
        json={"bugs": [bug.model_dump()]},
    )
    mocked_responses.add(
        responses.GET,
        f"{settings.bugzilla_base_url}/rest/bug/{bug.id}/comment",
        json={"bugs": {str(bug.id): {"comments": []}}},
    )
    mocked_responses.add(
        responses.POST,
        f"{settings.jira_base_url}rest/api/2/issue",
        json={"key": "TEST-1"},
    )
    mocked_responses.add(
        responses.POST,
        f"{settings.jira_base_url}rest/api/2/issue/TEST-1/remotelink",
        status=400,
        json={
            "errorMessages": [],
            "errors": {"resolution": "Field 'resolution' cannot be set."},
        },
    )

    await execute_or_queue(
        request=bugzilla_webhook_request, queue=dl_queue, actions=actions
    )

    items = (await dl_queue.retrieve())[bug.id]
    item = [i async for i in items][0]
    assert (
        item.error.description
        == "HTTP 400: resolution: Field 'resolution' cannot be set."
    )


def test_default_invalid_init():
    with pytest.raises(TypeError):
        Executor()


def test_unspecified_groups_come_from_default_steps(action_params_factory):
    action = Executor(action_params_factory(steps={"comment": ["create_comment"]}))

    assert len(action.steps) == 3
    assert action.steps


def test_default_returns_callable_without_data(action_params):
    callable_object = Executor(action_params)
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
    mocked_jira.create_issue.return_value = {"key": "k"}
    mocked_jira.create_or_update_issue_remote_links.return_value = {"foo": "bar"}
    mocked_bugzilla.get_bug.return_value = context_create_example.bug
    mocked_bugzilla.get_comments.return_value = []
    callable_object = Executor(
        action_params_factory(jira_project_key=context_create_example.jira.project)
    )

    handled, details = callable_object(context=context_create_example)

    assert handled
    assert details["responses"][0] == {"key": "k"}
    assert details["responses"][1] == {"foo": "bar"}


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


def test_step_function_counter_incremented_for_success(
    action_params_factory, action_context_factory
):
    context = action_context_factory(operation=Operation.CREATE)
    executor = Executor(action_params_factory(steps={"new": ["create_issue"]}))
    with mock.patch("jbi.runner.statsd") as mocked:
        executor(context=context)
    mocked.incr.assert_called_with("jbi.steps.create_issue.count")


def test_step_function_counter_not_incremented_for_noop(
    action_params_factory, action_context_factory
):
    context = action_context_factory(operation=Operation.UPDATE, jira__issue="JBI-234")
    assert not context.event.changed_fields()
    executor = Executor(
        action_params_factory(steps={"existing": ["update_issue_summary"]})
    )
    # update_issue_summary without a changed summary will result in a NOOP
    with mock.patch("jbi.runner.statsd") as mocked:
        executor(context=context)
    mocked.incr.assert_not_called()


def test_counter_is_incremented_for_create(
    webhook_request_factory, actions, mocked_bugzilla, bug_factory
):
    webhook_payload = webhook_request_factory(
        event__target="bug",
        bug__see_also=[],
    )
    mocked_bugzilla.get_bug.return_value = webhook_payload.bug
    with mock.patch("jbi.runner.statsd") as mocked:
        execute_action(request=webhook_payload, actions=actions)
    mocked.incr.assert_any_call("jbi.operation.create.count")


def test_counter_is_incremented_for_update(
    actions, webhook_request_factory, mocked_bugzilla, mocked_jira
):
    webhook_payload = webhook_request_factory(
        event__target="bug",
        bug__see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
    )
    mocked_bugzilla.get_bug.return_value = webhook_payload.bug
    mocked_jira.get_issue.return_value = {"fields": {"project": {"key": "JBI"}}}
    with mock.patch("jbi.runner.statsd") as mocked:
        execute_action(request=webhook_payload, actions=actions)
    mocked.incr.assert_any_call("jbi.operation.update.count")


def test_counter_is_incremented_for_comment(
    actions, webhook_request_factory, mocked_bugzilla, mocked_jira
):
    webhook_payload = webhook_request_factory(
        event__target="comment",
        bug__see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
    )
    mocked_bugzilla.get_bug.return_value = webhook_payload.bug
    mocked_jira.get_issue.return_value = {"fields": {"project": {"key": "JBI"}}}
    with mock.patch("jbi.runner.statsd") as mocked:
        execute_action(request=webhook_payload, actions=actions)
    mocked.incr.assert_any_call("jbi.operation.comment.count")


@pytest.mark.parametrize(
    "whiteboard",
    [
        "[DevTest]",
        "[DevTest-]",
        "[DevTest-test]",
        "[DevTest-test-foo]",
        "[example][DevTest]",
        "[DevTest][example]",
        "[example][DevTest][example]",
    ],
)
def test_lookup_action_found(whiteboard, actions, bug_factory):
    bug = bug_factory(id=1234, whiteboard=whiteboard)
    action = lookup_action(bug, actions)
    assert action.whiteboard_tag == "devtest"
    assert "test config" in action.description


@pytest.mark.parametrize(
    "whiteboard",
    [
        "DevTest",
        "[-DevTest-]",
        "[-DevTest]",
        "[test-DevTest]",
        "[foo-DevTest-bar]",
        "[foo-bar-DevTest-foo-bar]",
        "foo DevTest",
        "DevTest bar",
        "foo DevTest bar",
        "[fooDevTest]",
        "[foo DevTest]",
        "[DevTestbar]",
        "[DevTest bar]",
        "[fooDevTestbar]",
        "[fooDevTest-bar]",
        "[foo-DevTestbar]",
        "[foo] devtest [bar]",
    ],
)
def test_lookup_action_not_found(whiteboard, actions, bug_factory):
    bug = bug_factory(id=1234, whiteboard=whiteboard)
    with pytest.raises(ActionNotFoundError) as exc_info:
        lookup_action(bug, actions)
    assert str(exc_info.value) == "devtest"
