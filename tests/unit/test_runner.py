import logging
from unittest import mock

import pytest
import requests
import responses

import tests.fixtures.factories as factories
from jbi import Operation
from jbi.bugzilla.client import BugNotAccessibleError
from jbi.environment import get_settings
from jbi.errors import ActionNotFoundError, IgnoreInvalidRequestError
from jbi.models import ActionContext
from jbi.runner import (
    Actions,
    Executor,
    _tag_added_to_whiteboard,
    execute_action,
    execute_or_queue,
    lookup_actions,
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


def test_request_is_ignored_because_bug_cannot_be_fetched(
    webhook_request_factory,
    actions,
    mocked_bugzilla,
):
    webhook = webhook_request_factory()
    mocked_bugzilla.get_bug.side_effect = BugNotAccessibleError(
        "not authorized to access bug 12345"
    )

    with pytest.raises(IgnoreInvalidRequestError) as exc_info:
        execute_action(request=webhook, actions=actions)

    assert str(exc_info.value) == "not authorized to access bug 12345"


def test_request_if_bugzilla_is_down_and_bug_cannot_be_fetched(
    webhook_request_factory,
    actions,
    mocked_bugzilla,
):
    webhook = webhook_request_factory()
    mocked_bugzilla.get_bug.side_effect = requests.ConnectTimeout()

    with pytest.raises(requests.ConnectTimeout):
        execute_action(request=webhook, actions=actions)


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

    assert str(exc_info.value) == "restricted bugs are not supported: bug is private"


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
    assert capturelogs.records[-1].actions[0]["whiteboard_tag"] == "devtest"


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

    # Verify that the bug has a JBI link (matching project), but not for other projects
    assert webhook.bug.extract_from_see_also(project_key="JBI") == "JBI-234"
    assert webhook.bug.extract_from_see_also(project_key="foo") is None

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
    # Force an unexpected failure inside execute_action. This used to happen
    # implicitly, via the MagicMock bug returned by the refresh, but the
    # restriction guard now rejects that mock before it can blow up further
    # down -- so the failure is made explicit rather than incidental.
    with mock.patch("jbi.runner.execute_action", side_effect=ValueError("boom")):
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
    [item] = [i async for i in items]
    assert (
        item.error.description
        == "POST /rest/api/2/issue/TEST-1/remotelink -> HTTP 400: Field 'resolution' cannot be set."
    )


def test_default_invalid_init():
    with pytest.raises(TypeError):
        Executor()


def test_unspecified_groups_come_from_default_steps(action_params_factory):
    action = Executor(action_params_factory(steps={"comment": ["create_comment"]}))

    assert len(action.steps) == 4


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


def test_counter_is_incremented_for_attachment(
    actions, webhook_request_factory, mocked_bugzilla, mocked_jira
):
    webhook_payload = webhook_request_factory(
        event__target="attachment",
        bug__see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
    )
    mocked_bugzilla.get_bug.return_value = webhook_payload.bug
    mocked_jira.get_issue.return_value = {"fields": {"project": {"key": "JBI"}}}
    with mock.patch("jbi.runner.statsd") as mocked:
        execute_action(request=webhook_payload, actions=actions)
    mocked.incr.assert_any_call("jbi.operation.attachment.count")


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
    action = lookup_actions(bug, actions)[0]
    assert action.whiteboard_tag == "devtest"
    assert "test config" in action.description


@pytest.mark.parametrize(
    "whiteboard,expected_tags",
    [
        ("[example][DevTest]", ["devtest"]),
        ("[DevTest][example]", ["devtest"]),
        ("[example][DevTest][other]", ["devtest", "other"]),
    ],
)
def test_multiple_lookup_actions_found(whiteboard, expected_tags, bug_factory):
    actions = factories.ActionsFactory(
        root=[
            factories.ActionFactory(
                whiteboard_tag="devtest",
                bugzilla_user_id="tbd",
                description="test config",
            ),
            factories.ActionFactory(
                whiteboard_tag="other",
                bugzilla_user_id="tbd",
                description="test config",
            ),
        ]
    )
    bug = bug_factory(id=1234, whiteboard=whiteboard)
    acts = lookup_actions(bug, actions)
    assert len(acts) == len(expected_tags)
    looked_up_tags = [a.whiteboard_tag for a in acts]
    assert sorted(looked_up_tags) == sorted(expected_tags)
    assert all(["test config" == a.description for a in acts])


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
        lookup_actions(bug, actions)[0]
    assert str(exc_info.value) == "devtest"


def test_request_triggers_multiple_actions(
    webhook_request_factory,
    mocked_bugzilla,
):
    actions = factories.ActionsFactory(
        root=[
            factories.ActionFactory(
                whiteboard_tag="devtest",
                bugzilla_user_id="tbd",
                description="test config",
            ),
            factories.ActionFactory(
                whiteboard_tag="other",
                bugzilla_user_id="tbd",
                description="test config",
            ),
        ]
    )

    webhook = webhook_request_factory(bug__whiteboard="[devtest][other]")
    mocked_bugzilla.get_bug.return_value = webhook.bug

    details = execute_action(request=webhook, actions=actions)

    # Details has the following shape:
    # {'devtest': {'responses': [..]}, 'other': {'responses': [...]}}
    assert len(actions) == len(details)
    assert "devtest" in details
    assert "other" in details


def test_request_triggers_multiple_update_actions(
    webhook_request_factory,
    mocked_bugzilla,
    mocked_jira,
    webhook_event_change_factory,
):
    actions = factories.ActionsFactory(
        root=[
            factories.ActionFactory(
                whiteboard_tag="devtest",
                bugzilla_user_id="tbd",
                description="test config",
                parameters__jira_project_key="JBI",
                parameters__steps__existing=["maybe_update_issue_resolution"],
                parameters__resolution_map={
                    "FIXED": "Closed",
                },
            ),
            factories.ActionFactory(
                whiteboard_tag="other",
                bugzilla_user_id="tbd",
                description="test config",
                parameters__jira_project_key="DE",
                parameters__steps__existing=["maybe_update_issue_resolution"],
                parameters__resolution_map={
                    "FIXED": "Done",
                },
            ),
        ]
    )

    webhook = webhook_request_factory(
        bug__whiteboard="[devtest][other]",
        bug__see_also=[
            "https://mozilla.atlassian.net/browse/JBI-234",
            "https://mozilla.atlassian.net/browse/DE-567",
        ],
        bug__resolution="FIXED",
        event__changes=[
            webhook_event_change_factory(
                field="resolution", removed="OPEN", added="FIXED"
            )
        ],
    )
    mocked_bugzilla.get_bug.return_value = webhook.bug

    def side_effect_for_get_issue(issue_key):
        if issue_key.startswith("JBI-"):
            return {"fields": {"project": {"key": "JBI"}}}
        elif issue_key.startswith("DE-"):
            return {"fields": {"project": {"key": "DE"}}}

        return None

    mocked_jira.get_issue.side_effect = side_effect_for_get_issue

    details = execute_action(request=webhook, actions=actions)

    mocked_jira.update_issue_field.assert_any_call(
        key="JBI-234",
        fields={
            "resolution": {"name": "Closed"},
        },
    )
    mocked_jira.update_issue_field.assert_any_call(
        key="DE-567",
        fields={
            "resolution": {"name": "Done"},
        },
    )

    # Details has the following shape:
    # {'devtest': {'responses': [..]}, 'other': {'responses': [...]}}
    assert len(actions) == len(details)
    assert "devtest" in details
    assert "other" in details


# --- _tag_added_to_whiteboard tests ---


@pytest.mark.parametrize(
    "removed,added,expected",
    [
        # Tag absent in removed, present in added → newly added
        ("", "[devtest]", True),
        ("[other]", "[other][devtest]", True),
        ("[other]", "[devtest][other]", True),
        # Tag already present in removed → not newly added
        ("[devtest]", "[devtest][other]", False),
        ("[devtest-sprint1]", "[devtest]", False),
    ],
)
def test_tag_added_to_whiteboard(
    removed, added, expected, action_factory, webhook_event_change_factory
):
    action = action_factory(whiteboard_tag="devtest")
    event = mock.MagicMock()
    event.changes = [
        webhook_event_change_factory(field="whiteboard", removed=removed, added=added)
    ]
    assert _tag_added_to_whiteboard(action, event) is expected


def test_tag_added_to_whiteboard_no_whiteboard_change(
    action_factory, webhook_event_change_factory
):
    action = action_factory(whiteboard_tag="devtest")
    event = mock.MagicMock()
    event.changes = [
        webhook_event_change_factory(field="summary", removed="old", added="new")
    ]
    assert _tag_added_to_whiteboard(action, event) is False


def test_tag_added_to_whiteboard_no_changes(action_factory):
    action = action_factory(whiteboard_tag="devtest")
    event = mock.MagicMock()
    event.changes = None
    assert _tag_added_to_whiteboard(action, event) is False


def test_tag_added_to_bug_with_linked_issue_triggers_resync(
    actions,
    webhook_request_factory,
    webhook_event_change_factory,
    mocked_bugzilla,
    mocked_jira,
    settings,
):
    """When a whiteboard tag is added to a bug that already has a linked Jira issue,
    the runner should run CREATE steps (full field resync) rather than UPDATE steps."""
    jira_url = f"{settings.jira_base_url}browse/JBI-234"
    webhook = webhook_request_factory(
        bug__whiteboard="[devtest]",
        bug__see_also=[jira_url],
        event__changes=[
            webhook_event_change_factory(
                field="whiteboard", removed="", added="[devtest]"
            )
        ],
    )
    mocked_bugzilla.get_bug.return_value = webhook.bug
    mocked_jira.get_issue.return_value = {"fields": {"project": {"key": "JBI"}}}
    mocked_jira.create_issue.return_value = {"key": "JBI-234"}

    execute_action(request=webhook, actions=actions)

    # create_issue must NOT have been called (issue already exists)
    mocked_jira.create_issue.assert_not_called()
    # update_issue_field IS called to sync the title (via update_issue_summary)
    mocked_jira.update_issue_field.assert_any_call(
        key="JBI-234", fields={"summary": mock.ANY}
    )


# --- R-01: Product/Component scope gate (plan D2) ---------------------------


@pytest.mark.parametrize(
    "scope,product,component,expected_sync",
    [
        # No scope configured: today's behavior, everything in scope.
        (None, "Core", "Machine Learning: On-Device", True),
        # Exact Product::Component match.
        (
            ["Core::Machine Learning: On-Device"],
            "Core",
            "Machine Learning: On-Device",
            True,
        ),
        # Bare product entry matches every component of that product.
        (["Core"], "Core", "Some Other Component", True),
        # Case-insensitive: BMO names are display strings.
        (
            ["core::machine learning: on-device"],
            "Core",
            "Machine Learning: On-Device",
            True,
        ),
        # Out of scope: same product, different component.
        (
            ["Core::Machine Learning: On-Device"],
            "Core",
            "Networking",
            False,
        ),
        # Out of scope: different product entirely.
        (["Core"], "Firefox", "General", False),
    ],
)
def test_product_component_scope_gate(
    webhook_request_factory,
    action_factory,
    mocked_jira,
    mocked_bugzilla,
    scope,
    product,
    component,
    expected_sync,
):
    action = action_factory(
        whiteboard_tag="devtest",
        parameters__jira_project_key="JBI",
        parameters__sync_products_components=scope,
    )
    actions = Actions(root=[action])
    webhook = webhook_request_factory(
        bug__product=product,
        bug__component=component,
        bug__see_also=[],
    )
    mocked_bugzilla.get_bug.return_value = webhook.bug

    if expected_sync:
        execute_action(request=webhook, actions=actions)
        assert mocked_jira.create_issue.called
    else:
        with pytest.raises(IgnoreInvalidRequestError) as exc_info:
            execute_action(request=webhook, actions=actions)
        assert "out of scope" in str(exc_info.value)
        assert not mocked_jira.create_issue.called


def test_scope_gate_keeps_in_scope_actions_when_another_is_filtered_out(
    webhook_request_factory, action_factory, mocked_jira, mocked_bugzilla
):
    """A bug matching two tags must still sync through the action whose scope
    covers it, even when the other action's scope excludes the bug."""
    in_scope = action_factory(
        whiteboard_tag="devtest",
        parameters__jira_project_key="JBI",
        parameters__sync_products_components=["Core"],
    )
    out_of_scope = action_factory(
        whiteboard_tag="other",
        parameters__jira_project_key="OTHER",
        parameters__sync_products_components=["Firefox"],
    )
    actions = Actions(root=[in_scope, out_of_scope])
    webhook = webhook_request_factory(
        bug__product="Core",
        bug__component="General",
        bug__whiteboard="[devtest][other]",
        bug__see_also=[],
    )
    mocked_bugzilla.get_bug.return_value = webhook.bug

    details = execute_action(request=webhook, actions=actions)

    assert list(details.keys()) == ["devtest"]


def test_scope_gate_uses_refreshed_bug_data(
    webhook_request_factory, action_factory, mocked_jira, mocked_bugzilla, bug_factory
):
    """Scope is evaluated on the refreshed bug: a bug whose component was moved
    into scope after the webhook fired must sync."""
    action = action_factory(
        whiteboard_tag="devtest",
        parameters__jira_project_key="JBI",
        parameters__sync_products_components=["Core::Machine Learning: On-Device"],
    )
    actions = Actions(root=[action])
    webhook = webhook_request_factory(bug__product="Core", bug__component="Networking")
    mocked_bugzilla.get_bug.return_value = bug_factory(
        id=webhook.bug.id,
        product="Core",
        component="Machine Learning: On-Device",
        see_also=[],
    )

    execute_action(request=webhook, actions=actions)

    assert mocked_jira.create_issue.called


# --- R-04: priority/severity threshold (plan D3) ----------------------------


@pytest.mark.parametrize(
    "min_priority,min_severity,priority,severity,expected_create",
    [
        # No thresholds configured: today's behavior.
        (None, None, "", "--", True),
        # At or above the priority bar.
        ("P2", None, "P1", "--", True),
        ("P2", None, "P2", "--", True),
        # Below the priority bar.
        ("P2", None, "P3", "--", False),
        # Unset priority counts as below the bar (pre-triage bugs stay out).
        ("P2", None, "", "--", False),
        ("P2", None, "--", "--", False),
        # Severity behaves the same way.
        (None, "S2", "", "S1", True),
        (None, "S2", "", "S3", False),
        (None, "S2", "", "N/A", False),
        # Both configured: both must be met.
        ("P2", "S2", "P1", "S1", True),
        ("P2", "S2", "P1", "S3", False),
        ("P2", "S2", "P3", "S1", False),
    ],
)
def test_priority_severity_threshold_gate(
    webhook_request_factory,
    action_factory,
    mocked_jira,
    mocked_bugzilla,
    min_priority,
    min_severity,
    priority,
    severity,
    expected_create,
):
    action = action_factory(
        whiteboard_tag="devtest",
        parameters__jira_project_key="JBI",
        parameters__min_priority=min_priority,
        parameters__min_severity=min_severity,
    )
    actions = Actions(root=[action])
    webhook = webhook_request_factory(
        bug__priority=priority, bug__severity=severity, bug__see_also=[]
    )
    mocked_bugzilla.get_bug.return_value = webhook.bug

    execute_action(request=webhook, actions=actions)

    assert mocked_jira.create_issue.called is expected_create


def test_threshold_gate_does_not_apply_to_already_linked_bugs(
    webhook_request_factory,
    action_factory,
    mocked_jira,
    mocked_bugzilla,
    settings,
):
    """Once a bug has a linked Jira issue we keep syncing it even if it is
    below the threshold: silently stranding an existing issue is worse than
    never having created it."""
    action = action_factory(
        whiteboard_tag="devtest",
        parameters__jira_project_key="JBI",
        parameters__min_priority="P1",
    )
    actions = Actions(root=[action])
    webhook = webhook_request_factory(
        bug__priority="P5",
        bug__see_also=[f"{settings.jira_base_url}browse/JBI-234"],
        event__action="modify",
        event__routing_key="bug.modify:assigned_to",
        event__changes=[
            factories.WebhookEventChangeFactory(
                field="summary", removed="old", added="new"
            )
        ],
    )
    mocked_bugzilla.get_bug.return_value = webhook.bug
    mocked_jira.get_issue.return_value = {"fields": {"project": {"key": "JBI"}}}

    execute_action(request=webhook, actions=actions)

    assert not mocked_jira.create_issue.called
    assert mocked_jira.update_issue_field.called


def test_below_threshold_bug_is_logged_as_ignored(
    webhook_request_factory, action_factory, mocked_jira, mocked_bugzilla, capturelogs
):
    action = action_factory(
        whiteboard_tag="devtest",
        parameters__jira_project_key="JBI",
        parameters__min_priority="P1",
    )
    actions = Actions(root=[action])
    webhook = webhook_request_factory(bug__priority="P4", bug__see_also=[])
    mocked_bugzilla.get_bug.return_value = webhook.bug

    with capturelogs.for_logger("jbi.runner").at_level(logging.INFO):
        execute_action(request=webhook, actions=actions)

    assert any(
        "below the sync threshold" in record.message for record in capturelogs.records
    )
    assert not mocked_jira.create_issue.called


# --- Invariant C, BMO side: forward-path echo gate (plan D6b) ---------------


def test_event_authored_by_jbi_is_ignored(
    webhook_request_factory, actions, mocked_jira, mocked_bugzilla, settings
):
    """A reverse write into BMO fires this webhook like any human edit. Without
    this gate it would bounce straight back into Jira."""
    webhook = webhook_request_factory(event__user__login="jbi-bot@mozilla.bugs")
    mocked_bugzilla.get_bug.return_value = webhook.bug

    with mock.patch.object(settings, "bugzilla_bot_login", "jbi-bot@mozilla.bugs"):
        with mock.patch("jbi.runner.settings", settings):
            with pytest.raises(IgnoreInvalidRequestError) as exc_info:
                execute_action(request=webhook, actions=actions)

    assert "authored by JBI itself" in str(exc_info.value)
    assert not mocked_jira.create_issue.called
    assert not mocked_jira.update_issue_field.called


def test_event_authored_by_a_human_is_not_suppressed(
    webhook_request_factory, actions, mocked_jira, mocked_bugzilla, settings
):
    webhook = webhook_request_factory(
        event__user__login="person@mozilla.com", bug__see_also=[]
    )
    mocked_bugzilla.get_bug.return_value = webhook.bug

    with mock.patch.object(settings, "bugzilla_bot_login", "jbi-bot@mozilla.bugs"):
        with mock.patch("jbi.runner.settings", settings):
            execute_action(request=webhook, actions=actions)

    assert mocked_jira.create_issue.called


def test_actorless_event_is_not_suppressed(
    webhook_request_factory, actions, mocked_jira, mocked_bugzilla, settings
):
    """`WebhookEvent.user` is optional, and an admin- or migration-driven
    change can arrive without one. Those fail open; D7's read-before-write is
    the backstop for the echo case."""
    webhook = webhook_request_factory(event__user=None, bug__see_also=[])
    mocked_bugzilla.get_bug.return_value = webhook.bug

    with mock.patch.object(settings, "bugzilla_bot_login", "jbi-bot@mozilla.bugs"):
        with mock.patch("jbi.runner.settings", settings):
            execute_action(request=webhook, actions=actions)

    assert mocked_jira.create_issue.called


def test_no_suppression_when_bot_login_is_unconfigured(
    webhook_request_factory, actions, mocked_jira, mocked_bugzilla, settings
):
    """Unset `bugzilla_bot_login` (today's deployed state) must suppress
    nothing at all."""
    webhook = webhook_request_factory(
        event__user__login="anyone@mozilla.com", bug__see_also=[]
    )
    mocked_bugzilla.get_bug.return_value = webhook.bug

    assert settings.bugzilla_bot_login is None

    execute_action(request=webhook, actions=actions)

    assert mocked_jira.create_issue.called


# --- Restricted bugs never reach Jira (security hardening) ------------------


def test_group_restricted_bug_is_not_synced(
    webhook_request_factory, actions, mocked_jira, mocked_bugzilla
):
    """`groups` is how BMO marks security/embargoed bugs. Checking only
    `is_private` -- an optional payload field -- would let these through."""
    webhook = webhook_request_factory(
        bug__is_private=False, bug__groups=["core-security"]
    )
    mocked_bugzilla.get_bug.return_value = webhook.bug

    with pytest.raises(IgnoreInvalidRequestError) as exc_info:
        execute_action(request=webhook, actions=actions)

    assert "core-security" in str(exc_info.value)
    assert not mocked_jira.create_issue.called
    assert not mocked_jira.update_issue_field.called


def test_bug_missing_is_private_but_in_groups_is_not_synced(
    webhook_request_factory, actions, mocked_jira, mocked_bugzilla
):
    """`is_private` is Optional, so an absent value reads as False. The
    groups check is what makes that safe."""
    webhook = webhook_request_factory(bug__is_private=None, bug__groups=["secure"])
    mocked_bugzilla.get_bug.return_value = webhook.bug

    with pytest.raises(IgnoreInvalidRequestError):
        execute_action(request=webhook, actions=actions)

    assert not mocked_jira.create_issue.called


def test_bug_restricted_after_the_webhook_fired_is_not_synced(
    webhook_request_factory, actions, mocked_jira, mocked_bugzilla, bug_factory
):
    """The payload says public, the refreshed bug says restricted -- the case
    the dead-letter queue makes likely, since an item can sit there for days.
    `BugNotAccessibleError` does not cover it: a bug restricted to a group JBI
    belongs to stays perfectly readable."""
    webhook = webhook_request_factory(bug__is_private=False, bug__groups=[])
    mocked_bugzilla.get_bug.return_value = bug_factory(
        id=webhook.bug.id, whiteboard="[devtest]", groups=["core-security"]
    )

    with pytest.raises(IgnoreInvalidRequestError) as exc_info:
        execute_action(request=webhook, actions=actions)

    assert "core-security" in str(exc_info.value)
    assert not mocked_jira.create_issue.called


def test_a_real_restricted_bug_payload_is_rejected(
    webhook_request_factory, actions, mocked_jira, mocked_bugzilla
):
    """The shape BMO actually sends for a group-restricted bug, captured from
    bugzilla-dev: `is_private: true`, **no** `groups` key, and the summary
    redacted to null. The REST API reports the opposite for the same bug
    (`is_private: None` with `groups` populated), which is why the guard
    checks both signals rather than picking one."""
    webhook = webhook_request_factory(
        bug__is_private=True,
        bug__groups=None,
        bug__summary=None,
        bug__whiteboard="[devtest]",
    )
    mocked_bugzilla.get_bug.return_value = webhook.bug

    with pytest.raises(IgnoreInvalidRequestError) as exc_info:
        execute_action(request=webhook, actions=actions)

    assert "restricted" in str(exc_info.value)
    # Crucially, rejected *before* the refresh: JBI's own account may belong
    # to the group, so a re-fetch would return the full, unredacted bug.
    assert not mocked_bugzilla.get_bug.called
    assert not mocked_jira.create_issue.called
