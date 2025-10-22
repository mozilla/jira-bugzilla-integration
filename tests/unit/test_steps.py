import logging
from unittest import mock

import pytest
import requests

from jbi import Operation, steps
from jbi.jira import JiraService
from jbi.jira.client import JiraCreateError
from jbi.models import ActionContext, JiraComponents
from jbi.runner import Executor

ALL_STEPS = {
    "new": [
        "create_issue",
        "maybe_delete_duplicate",
        "add_link_to_bugzilla",
        "add_link_to_jira",
        "maybe_assign_jira_user",
        "maybe_update_issue_status",
        "maybe_update_issue_resolution",
    ],
    "existing": [
        "update_issue_summary",
        "maybe_assign_jira_user",
        "maybe_update_issue_status",
        "maybe_update_issue_resolution",
        "maybe_add_phabricator_link",
    ],
    "comment": [
        "create_comment",
    ],
}


def test_created_public(
    context_create_example: ActionContext,
    mocked_jira,
    mocked_bugzilla,
    action_params_factory,
    comment_factory,
    settings,
):
    mocked_jira.create_issue.return_value = {"key": "k"}
    mocked_bugzilla.get_bug.return_value = context_create_example.bug
    mocked_bugzilla.get_comments.return_value = [
        comment_factory(text="Initial comment")
    ]
    callable_object = Executor(
        action_params_factory(jira_project_key=context_create_example.jira.project)
    )

    callable_object(context=context_create_example)

    mocked_jira.create_issue.assert_called_once_with(
        fields={
            "summary": "JBI Test",
            "issuetype": {"name": "Bug"},
            "description": "Initial comment",
            "project": {"key": "JBI"},
        },
    )

    mocked_bugzilla.update_bug.assert_called_once_with(
        654321, see_also={"add": [f"{settings.jira_base_url}browse/k"]}
    )


def test_created_with_custom_issue_type_and_fallback(
    action_context_factory,
    mocked_jira,
    mocked_bugzilla,
    action_params_factory,
    comment_factory,
):
    action_context = action_context_factory(
        operation=Operation.CREATE, bug__type="enhancement"
    )
    mocked_jira.create_issue.return_value = {"key": "k"}
    mocked_bugzilla.get_bug.return_value = action_context.bug
    mocked_bugzilla.get_comments.return_value = [
        comment_factory(text="Initial comment")
    ]

    callable_object = Executor(
        action_params_factory(
            jira_project_key=action_context.jira.project,
            steps={"new": ["create_issue"]},
            issue_type_map={
                "task": "Epic",
            },
        )
    )

    callable_object(context=action_context)

    mocked_jira.create_issue.assert_called_once_with(
        fields={
            "summary": "JBI Test",
            "issuetype": {"name": "Task"},
            "description": "Initial comment",
            "project": {"key": "JBI"},
        },
    )


def test_created_with_custom_issue_type(
    action_context_factory,
    mocked_jira,
    mocked_bugzilla,
    action_params_factory,
    comment_factory,
):
    action_context = action_context_factory(
        operation=Operation.CREATE, bug__type="task"
    )
    mocked_jira.create_issue.return_value = {"key": "k"}
    mocked_bugzilla.get_bug.return_value = action_context.bug
    mocked_bugzilla.get_comments.return_value = [
        comment_factory(text="Initial comment")
    ]

    callable_object = Executor(
        action_params_factory(
            jira_project_key=action_context.jira.project,
            steps={"new": ["create_issue"]},
            issue_type_map={
                "task": "Epic",
            },
        )
    )

    callable_object(context=action_context)

    mocked_jira.create_issue.assert_called_once_with(
        fields={
            "summary": "JBI Test",
            "issuetype": {"name": "Epic"},
            "description": "Initial comment",
            "project": {"key": "JBI"},
        },
    )


def test_modified_public(
    mocked_jira,
    action_context_factory,
    webhook_event_change_factory,
    action_params_factory,
):
    action_context = action_context_factory(
        operation=Operation.UPDATE,
        bug__see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
        bug__summary="[JBI] (Test)",
        jira__issue="JBI-234",
        event__changes=[
            webhook_event_change_factory(
                field="summary", removed="", added="[JBI] (Test)"
            )
        ],
    )

    callable_object = Executor(
        action_params_factory(jira_project_key=action_context.jira.project)
    )

    callable_object(context=action_context)

    assert action_context.bug.extract_from_see_also(
        project_key=action_context.jira.project
    ), "see_also is not empty"

    mocked_jira.update_issue_field.assert_called_once_with(
        key="JBI-234",
        fields={"summary": "[JBI] (Test)"},
    )


def test_comment_for_modified_assignee_and_status(
    mocked_jira,
    action_params_factory,
    webhook_event_factory,
    action_context_factory,
    bug_factory,
    jira_context_factory,
):
    bug = bug_factory(see_also=["https://mozilla.atlassian.net/browse/JBI-234"])
    changes = [
        {
            "field": "status",
            "removed": "OPEN",
            "added": "FIXED",
        },
        {
            "field": "assignee",
            "removed": "nobody@mozilla.org",
            "added": "mathieu@mozilla.com",
        },
    ]
    event = webhook_event_factory(routing_key="bug.modify", changes=changes)
    context = action_context_factory(
        operation=Operation.UPDATE,
        bug=bug,
        event=event,
        jira=jira_context_factory(issue=bug.extract_from_see_also(project_key="JBI")),
    )

    callable_object = Executor(
        action_params_factory(jira_project_key=context.jira.project)
    )
    callable_object(context=context)

    mocked_jira.issue_add_comment.assert_any_call(
        issue_key="JBI-234",
        comment='{\n    "assignee": "nobody@mozilla.org"\n}',
    )
    mocked_jira.issue_add_comment.assert_any_call(
        issue_key="JBI-234",
        comment='{\n    "modified by": "nobody@mozilla.org",\n    "resolution": "",\n    "status": "NEW"\n}',
    )


def test_added_comment(
    context_comment_example: ActionContext, mocked_jira, action_params_factory
):
    callable_object = Executor(
        action_params_factory(jira_project_key=context_comment_example.jira.project)
    )

    callable_object(context=context_comment_example)

    mocked_jira.issue_add_comment.assert_called_once_with(
        issue_key="JBI-234",
        comment="*mathieu@mozilla.org* commented: \nbq. hello\nworld",
    )


def test_added_attachment(
    context_attachment_example: ActionContext, mocked_jira, action_params_factory
):
    callable_object = Executor(
        action_params_factory(jira_project_key=context_attachment_example.jira.project)
    )

    callable_object(context=context_attachment_example)

    mocked_jira.issue_add_comment.assert_called_once_with(
        issue_key="JBI-234",
        comment="*phab-bot@bmo.tld* created an attachment:\n*Description*: Bug 1337 - Stop war r?peace\n*Filename*: phabricator-D1234-url.txt (text/x-phabricator-request)\n*Phabricator URL*: https://phabricator.services.mozilla.com/D1234",
    )

def test_added_phabricator_attachment(
    action_context_factory, mocked_jira, action_params_factory
):
    phabricator_attachment_context = action_context_factory(
        operation=Operation.ATTACHMENT,
        bug__with_attachment=True,
        bug__id=5555,
        bug__attachment__is_patch=True,
        bug__attachment__is_obsolete=False,
        bug__attachment__id=123456,
        bug__attachment__file_name="phabricator-D1234-url.txt",
        bug__attachment__description="Bug 1234 - Fix all the bugs",
        bug__attachment__content_type="text/x-phabricator-request",
        event__target="attachment",
        jira__issue="JBI-234",
    )
    callable_object = Executor(
        action_params_factory(jira_project_key=phabricator_attachment_context.jira.project)
    )

    callable_object(context=phabricator_attachment_context)

    mocked_jira.create_or_update_issue_remote_links.assert_called_once_with(
        issue_key="JBI-234",
        link_url="https://phabricator.services.mozilla.com/D1234",
        title="Bug 1234 - Fix all the bugs",
        global_id="5555-123456",
    )


def test_empty_comment_not_added(
    action_context_factory, mocked_jira, action_params_factory
):
    empty_comment_context = action_context_factory(
        operation=Operation.COMMENT,
        bug__see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
        bug__with_comment=True,
        bug__comment__number=2,
        bug__comment__body="",
        bug__comment__is_private=False,
        event__target="comment",
        event__user__login="mathieu@mozilla.org",
        jira__issue="JBI-234",
    )

    callable_object = Executor(
        action_params_factory(jira_project_key=empty_comment_context.jira.project)
    )

    callable_object(context=empty_comment_context)

    mocked_jira.issue_add_comment.assert_not_called()


def test_jira_returns_an_error(
    context_create_example: ActionContext, mocked_jira, action_params_factory
):
    mocked_jira.create_issue.side_effect = [JiraCreateError("Boom")]
    callable_object = Executor(
        action_params_factory(jira_project_key=context_create_example.jira.project)
    )

    with pytest.raises(JiraCreateError) as exc_info:
        callable_object(context=context_create_example)

    assert str(exc_info.value) == "Boom"


def test_create_with_no_assignee(
    context_create_example: ActionContext,
    mocked_jira,
    mocked_bugzilla,
    action_params_factory,
    comment_factory,
):
    mocked_bugzilla.get_bug.return_value = context_create_example.bug
    mocked_bugzilla.get_comments.return_value = [
        comment_factory(text="Initial `comment`")
    ]
    mocked_jira.create_issue.return_value = {"key": "new-id"}
    callable_object = Executor(
        action_params_factory(
            jira_project_key=context_create_example.jira.project, steps=ALL_STEPS
        )
    )
    handled, _ = callable_object(context=context_create_example)

    assert handled
    mocked_jira.create_issue.assert_called_once_with(
        fields={
            "summary": "JBI Test",
            "issuetype": {"name": "Bug"},
            "description": "Initial {{comment}}",
            "project": {"key": "JBI"},
        },
    )
    mocked_jira.user_find_by_user_string.assert_not_called()
    mocked_jira.update_issue_field.assert_not_called()
    mocked_jira.set_issue_status.assert_not_called()


def test_create_with_assignee(
    action_context_factory,
    mocked_jira,
    mocked_bugzilla,
    action_params_factory,
    comment_factory,
):
    action_context = action_context_factory(
        operation=Operation.CREATE,
        bug__assigned_to="dtownsend@mozilla.com",
    )
    # Make sure the bug fetched the second time in `create_and_link_issue()` also has the assignee.
    mocked_bugzilla.get_bug.return_value = action_context.bug
    mocked_jira.create_issue.return_value = {"key": "JBI-534"}
    mocked_jira.user_find_by_user_string.return_value = [{"accountId": "6254"}]
    mocked_bugzilla.get_comments.return_value = [
        comment_factory(text="Initial comment")
    ]

    callable_object = Executor(
        action_params_factory(
            jira_project_key=action_context.jira.project, steps=ALL_STEPS
        )
    )
    callable_object(context=action_context)

    mocked_jira.create_issue.assert_called_once_with(
        fields={
            "summary": "JBI Test",
            "issuetype": {"name": "Bug"},
            "description": "Initial comment",
            "project": {"key": "JBI"},
        },
    )
    mocked_jira.user_find_by_user_string.assert_called_once_with(
        query="dtownsend@mozilla.com"
    )
    mocked_jira.update_issue_field.assert_called_once_with(
        key="JBI-534",
        fields={"assignee": {"accountId": "6254"}},
    )
    mocked_jira.set_issue_status.assert_not_called()


def test_clear_assignee(
    action_context_factory,
    mocked_jira,
    action_params_factory,
    webhook_event_change_factory,
):
    action_context = action_context_factory(
        operation=Operation.UPDATE,
        bug__see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
        jira__issue="JBI-234",
        event__action="modify",
        event__changes=[
            webhook_event_change_factory(field="assigned_to", removed="user", added="")
        ],
    )

    callable_object = Executor(
        action_params_factory(
            jira_project_key=action_context.jira.project, steps=ALL_STEPS
        )
    )
    callable_object(context=action_context)

    mocked_jira.user_find_by_user_string.assert_not_called()
    mocked_jira.update_issue_field.assert_any_call(
        key="JBI-234",
        fields={"assignee": None},
    )


def test_set_assignee(
    action_context_factory: ActionContext,
    mocked_jira,
    action_params_factory,
    webhook_event_change_factory,
):
    action_context = action_context_factory(
        operation=Operation.UPDATE,
        bug__see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
        bug__assigned_to="dtownsend@mozilla.com",
        jira__issue="JBI-234",
        event__action="modify",
        event__changes=[
            webhook_event_change_factory(
                field="assigned_to", removed="", added="dtownsend@mozilla.com"
            )
        ],
    )

    mocked_jira.user_find_by_user_string.return_value = [{"accountId": "6254"}]

    callable_object = Executor(
        action_params_factory(
            jira_project_key=action_context.jira.project, steps=ALL_STEPS
        )
    )
    callable_object(context=action_context)

    mocked_jira.create_issue.assert_not_called()
    mocked_jira.user_find_by_user_string.assert_called_once_with(
        query="dtownsend@mozilla.com"
    )
    mocked_jira.update_issue_field.assert_any_call(
        key="JBI-234",
        fields={"assignee": {"accountId": "6254"}},
    )
    mocked_jira.set_issue_status.assert_not_called()


def test_set_assignee_failing_create(
    action_context_factory,
    mocked_jira,
    capturelogs,
):
    action_context = action_context_factory(
        operation=Operation.CREATE,
        jira__issue="key",
        bug__assigned_to="postmaster@localhost",
    )
    mocked_jira.update_issue_field.side_effect = requests.exceptions.HTTPError(
        "unknown user", response=mock.MagicMock(status_code=400)
    )

    with capturelogs.for_logger("jbi.steps").at_level(logging.DEBUG):
        result, _ = steps.maybe_assign_jira_user(
            context=action_context, jira_service=JiraService(mocked_jira)
        )
        assert result == steps.StepStatus.INCOMPLETE

    assert capturelogs.messages == ["User postmaster@localhost not found"]


def test_set_assignee_failing_update(
    action_context_factory,
    mocked_jira,
    capturelogs,
    webhook_event_change_factory,
):
    mocked_jira.user_find_by_user_string.return_value = []
    action_context = action_context_factory(
        operation=Operation.UPDATE,
        current_step="maybe_assign_jira_user",
        jira__issue="key",
        bug__assigned_to="postmaster@localhost",
        bug__see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
        event__action="modify",
        event__changes=[
            webhook_event_change_factory(
                field="assigned_to", removed="", added="postmaster@localhost"
            )
        ],
    )

    with capturelogs.for_logger("jbi.steps").at_level(logging.DEBUG):
        steps.maybe_assign_jira_user(
            context=action_context, jira_service=JiraService(mocked_jira)
        )

    assert capturelogs.messages == ["User postmaster@localhost not found"]
    # Assignee is cleared if failed to update
    mocked_jira.update_issue_field.assert_any_call(
        key="key",
        fields={"assignee": None},
    )


def test_create_with_unknown_status(
    action_context_factory,
    mocked_jira,
    mocked_bugzilla,
    action_params_factory,
    comment_factory,
):
    action_context = action_context_factory(
        operation=Operation.CREATE, bug__status="NEW", bug__resolution=""
    )

    mocked_bugzilla.get_bug.return_value = action_context.bug
    mocked_bugzilla.get_comments.return_value = [
        comment_factory(text="Initial comment")
    ]
    mocked_jira.create_issue.return_value = {"key": "new-id"}

    callable_object = Executor(
        action_params_factory(
            jira_project_key=action_context.jira.project,
            steps=ALL_STEPS,
            status_map={
                "ASSIGNED": "In Progress",
                "FIXED": "Closed",
            },
        )
    )
    callable_object(context=action_context)

    mocked_jira.create_issue.assert_called_once_with(
        fields={
            "summary": "JBI Test",
            "issuetype": {"name": "Bug"},
            "description": "Initial comment",
            "project": {"key": "JBI"},
        },
    )
    mocked_jira.user_find_by_user_string.assert_not_called()
    mocked_jira.update_issue_field.assert_not_called()
    mocked_jira.set_issue_status.assert_not_called()


def test_create_with_known_status(
    action_context_factory,
    mocked_jira,
    mocked_bugzilla,
    action_params_factory,
    comment_factory,
):
    action_context = action_context_factory(
        operation=Operation.CREATE, bug__status="ASSIGNED", bug__resolution=""
    )

    # Make sure the bug fetched the second time in `create_and_link_issue()` also has the status.
    mocked_bugzilla.get_bug.return_value = action_context.bug
    mocked_bugzilla.get_comments.return_value = [
        comment_factory(text="Initial comment")
    ]
    mocked_jira.create_issue.return_value = {"key": "JBI-534"}

    callable_object = Executor(
        action_params_factory(
            jira_project_key=action_context.jira.project,
            steps=ALL_STEPS,
            status_map={
                "ASSIGNED": "In Progress",
                "FIXED": "Closed",
            },
        )
    )
    callable_object(context=action_context)

    mocked_jira.create_issue.assert_called_once_with(
        fields={
            "summary": "JBI Test",
            "issuetype": {"name": "Bug"},
            "description": "Initial comment",
            "project": {"key": "JBI"},
        },
    )
    mocked_jira.user_find_by_user_string.assert_not_called()
    mocked_jira.update_issue_field.assert_not_called()
    mocked_jira.set_issue_status.assert_called_once_with("JBI-534", "In Progress")


def test_change_to_unknown_status(
    action_context_factory,
    mocked_jira,
    capturelogs,
    action_params_factory,
):
    action_context = action_context_factory(
        operation=Operation.UPDATE,
        bug__see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
        jira__issue="JBI-234",
        bug__status="NEW",
        bug__resolution="",
        event__action="modify",
        event__routing_key="bug.modify:status",
    )

    with capturelogs.for_logger("jbi.steps").at_level(logging.DEBUG):
        action_params = action_params_factory(
            jira_project_key=action_context.jira.project,
            status_map={
                "ASSIGNED": "In Progress",
                "FIXED": "Closed",
            },
        )
        result, _ = steps.maybe_update_issue_status(
            action_context,
            parameters=action_params,
            jira_service=JiraService(mocked_jira),
        )

        mocked_jira.update_issue_field.assert_not_called()

        assert result == steps.StepStatus.INCOMPLETE
        assert capturelogs.messages == ["Bug status 'NEW' was not in the status map."]
        mocked_jira.create_issue.assert_not_called()
        mocked_jira.user_find_by_user_string.assert_not_called()
        mocked_jira.set_issue_status.assert_not_called()


def test_change_to_known_status(
    action_context_factory,
    mocked_jira,
    action_params_factory,
    webhook_event_change_factory,
):
    action_context = action_context_factory(
        operation=Operation.UPDATE,
        bug__see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
        jira__issue="JBI-234",
        bug__status="ASSIGNED",
        bug__resolution="",
        event__action="modify",
        event__changes=[
            webhook_event_change_factory(
                field="status", removed="NEW", added="ASSIGNED"
            )
        ],
    )

    callable_object = Executor(
        action_params_factory(
            jira_project_key=action_context.jira.project,
            steps=ALL_STEPS,
            status_map={
                "ASSIGNED": "In Progress",
                "FIXED": "Closed",
            },
        )
    )
    callable_object(context=action_context)

    mocked_jira.create_issue.assert_not_called()
    mocked_jira.user_find_by_user_string.assert_not_called()
    mocked_jira.set_issue_status.assert_called_once_with("JBI-234", "In Progress")


def test_change_to_known_resolution(
    action_context_factory,
    mocked_jira,
    action_params_factory,
    webhook_event_change_factory,
):
    action_context = action_context_factory(
        operation=Operation.UPDATE,
        current_step="maybe_update_issue_status",
        bug__see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
        jira__issue="JBI-234",
        bug__status="RESOLVED",
        bug__resolution="FIXED",
        event__action="modify",
        event__changes=[
            webhook_event_change_factory(
                field="resolution", removed="FIXED", added="OPEN"
            )
        ],
    )

    params = action_params_factory(
        jira_project_key=action_context.jira.project,
        status_map={
            "ASSIGNED": "In Progress",
            "FIXED": "Closed",
        },
    )
    steps.maybe_update_issue_status(
        action_context, parameters=params, jira_service=JiraService(mocked_jira)
    )

    mocked_jira.create_issue.assert_not_called()
    mocked_jira.user_find_by_user_string.assert_not_called()
    mocked_jira.set_issue_status.assert_called_once_with("JBI-234", "Closed")


def test_change_to_known_resolution_with_resolution_map(
    action_context_factory,
    webhook_event_change_factory,
    mocked_jira,
    action_params_factory,
):
    action_context = action_context_factory(
        operation=Operation.UPDATE,
        bug__see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
        bug__resolution="DUPLICATE",
        event__action="modify",
        event__changes=[
            webhook_event_change_factory(
                field="resolution", removed="OPEN", added="FIXED"
            )
        ],
        jira__issue="JBI-234",
    )

    callable_object = Executor(
        action_params_factory(
            jira_project_key=action_context.jira.project,
            steps=ALL_STEPS,
            resolution_map={
                "DUPLICATE": "Duplicate",
            },
        )
    )
    callable_object(context=action_context)

    mocked_jira.update_issue_field.assert_called_with(  # not once
        key="JBI-234",
        fields={
            "resolution": {"name": "Duplicate"},
        },
    )


def test_change_to_unknown_resolution_with_resolution_map(
    action_context_factory,
    webhook_event_change_factory,
    mocked_jira,
    capturelogs,
    action_params_factory,
):
    action_context = action_context_factory(
        operation=Operation.UPDATE,
        bug__see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
        bug__resolution="WONTFIX",
        event__action="modify",
        event__changes=[
            webhook_event_change_factory(
                field="resolution", removed="OPEN", added="FIXED"
            )
        ],
        jira__issue="JBI-234",
    )

    action_params = action_params_factory(
        jira_project_key=action_context.jira.project,
        resolution_map={
            "DUPLICATE": "Duplicate",
        },
    )

    with capturelogs.for_logger("jbi.steps").at_level(logging.DEBUG):
        result, _ = steps.maybe_update_issue_resolution(
            action_context,
            parameters=action_params,
            jira_service=JiraService(mocked_jira),
        )

    mocked_jira.update_issue_field.assert_not_called()
    assert result == steps.StepStatus.INCOMPLETE
    assert capturelogs.messages == [
        "Bug resolution 'WONTFIX' was not in the resolution map."
    ]


def test_create_issue_empty_priority(
    action_context_factory,
    mocked_jira,
    action_params_factory,
):
    action_context = action_context_factory(
        operation=Operation.CREATE, bug__priority=None
    )
    params = action_params_factory(
        jira_project_key=action_context.jira.project,
    )

    result, _ = steps.maybe_update_issue_priority(
        action_context, parameters=params, jira_service=JiraService(mocked_jira)
    )

    assert result == steps.StepStatus.NOOP


def test_update_issue_remove_priority(
    action_context_factory,
    mocked_jira,
    action_params_factory,
    webhook_event_change_factory,
):
    action_context = action_context_factory(
        operation=Operation.UPDATE,
        jira__issue="JBI-234",
        bug__priority="--",
        current_step="maybe_update_issue_priority",
        event__changes=[
            webhook_event_change_factory(field="priority", removed="P1", added="")
        ],
    )
    params = action_params_factory(
        jira_project_key=action_context.jira.project,
    )

    result, _ = steps.maybe_update_issue_priority(
        action_context, parameters=params, jira_service=JiraService(mocked_jira)
    )

    assert result == steps.StepStatus.SUCCESS
    mocked_jira.update_issue_field.assert_called_with(
        key="JBI-234", fields={"priority": None}
    )


def test_update_issue_priority(
    action_context_factory,
    mocked_jira,
    action_params_factory,
    webhook_event_change_factory,
):
    action_context = action_context_factory(
        operation=Operation.UPDATE,
        current_step="maybe_update_issue_priority",
        bug__see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
        jira__issue="JBI-234",
        bug__priority="P1",
        event__action="modify",
        event__changes=[
            webhook_event_change_factory(field="priority", removed="--", added="P1")
        ],
    )

    params = action_params_factory(
        jira_project_key=action_context.jira.project,
        priority_map={
            "P1": "Urgent",
        },
    )
    steps.maybe_update_issue_priority(
        action_context, parameters=params, jira_service=JiraService(mocked_jira)
    )

    mocked_jira.create_issue.assert_not_called()
    mocked_jira.update_issue_field.assert_called_with(
        key="JBI-234", fields={"priority": {"name": "Urgent"}}
    )


def test_update_issue_unknown_priority(
    action_context_factory,
    mocked_jira,
    action_params_factory,
    webhook_event_change_factory,
    capturelogs,
):
    action_context = action_context_factory(
        operation=Operation.UPDATE,
        current_step="maybe_update_issue_priority",
        bug__see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
        jira__issue="JBI-234",
        bug__priority="P1",
        event__action="modify",
        event__changes=[
            webhook_event_change_factory(field="priority", removed="--", added="P1")
        ],
    )

    params = action_params_factory(
        jira_project_key=action_context.jira.project,
        priority_map={
            "P5": "Later",
        },
    )
    with capturelogs.for_logger("jbi.steps").at_level(logging.DEBUG):
        result, _ = steps.maybe_update_issue_priority(
            action_context, parameters=params, jira_service=JiraService(mocked_jira)
        )

    assert result == steps.StepStatus.INCOMPLETE
    mocked_jira.update_issue_field.assert_not_called()
    assert capturelogs.messages == ["Bug priority 'P1' was not in the priority map."]


def test_update_issue_severity(
    action_context_factory,
    mocked_jira,
    action_params_factory,
    webhook_event_change_factory,
):
    action_context = action_context_factory(
        operation=Operation.UPDATE,
        current_step="maybe_update_issue_severity",
        bug__see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
        jira__issue="JBI-234",
        bug__severity="S3",
        event__action="modify",
        event__changes=[
            webhook_event_change_factory(field="severity", removed="--", added="S3")
        ],
    )

    params = action_params_factory(
        jira_project_key=action_context.jira.project,
        severity_map={
            "S3": "Moderate",
        },
    )
    steps.maybe_update_issue_severity(
        action_context, parameters=params, jira_service=JiraService(mocked_jira)
    )

    mocked_jira.create_issue.assert_not_called()
    mocked_jira.update_issue_field.assert_called_with(
        key="JBI-234", fields={"customfield_10319": {"value": "Moderate"}}
    )


def test_update_issue_unknown_severity(
    action_context_factory,
    mocked_jira,
    action_params_factory,
    webhook_event_change_factory,
    capturelogs,
):
    action_context = action_context_factory(
        operation=Operation.UPDATE,
        current_step="maybe_update_issue_severity",
        bug__see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
        jira__issue="JBI-234",
        bug__severity="S3",
        event__action="modify",
        event__changes=[
            webhook_event_change_factory(field="severity", removed="--", added="S3")
        ],
    )

    params = action_params_factory(
        jira_project_key=action_context.jira.project,
        severity_map={
            "S1": "High",
        },
    )
    with capturelogs.for_logger("jbi.steps").at_level(logging.DEBUG):
        result, _ = steps.maybe_update_issue_severity(
            action_context, parameters=params, jira_service=JiraService(mocked_jira)
        )

    assert result == steps.StepStatus.INCOMPLETE
    mocked_jira.update_issue_field.assert_not_called()
    assert capturelogs.messages == ["Bug severity 'S3' was not in the severity map."]


def test_update_issue_points(
    action_context_factory,
    mocked_jira,
    action_params_factory,
    webhook_event_change_factory,
):
    action_context = action_context_factory(
        operation=Operation.UPDATE,
        current_step="maybe_update_issue_points",
        bug__see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
        jira__issue="JBI-234",
        bug__cf_fx_points="15",
        event__action="modify",
        event__changes=[
            webhook_event_change_factory(field="cf_fx_points", removed="?", added="15")
        ],
    )

    params = action_params_factory(
        jira_project_key=action_context.jira.project,
    )
    steps.maybe_update_issue_points(
        action_context, parameters=params, jira_service=JiraService(mocked_jira)
    )

    mocked_jira.create_issue.assert_not_called()
    mocked_jira.update_issue_field.assert_called_with(
        key="JBI-234", fields={"customfield_10037": 15}
    )


def test_update_issue_points_removed(
    action_context_factory,
    mocked_jira,
    action_params_factory,
    webhook_event_change_factory,
):
    action_context = action_context_factory(
        operation=Operation.UPDATE,
        current_step="maybe_update_issue_points",
        bug__see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
        jira__issue="JBI-234",
        bug__cf_fx_points="---",
        event__action="modify",
        event__changes=[
            webhook_event_change_factory(field="cf_fx_points", removed="?", added="0")
        ],
    )

    params = action_params_factory(
        jira_project_key=action_context.jira.project,
    )
    steps.maybe_update_issue_points(
        action_context, parameters=params, jira_service=JiraService(mocked_jira)
    )

    mocked_jira.create_issue.assert_not_called()
    mocked_jira.update_issue_field.assert_called_with(
        key="JBI-234", fields={"customfield_10037": 0}
    )


def test_empty_issue_points_ignored_on_create(
    action_context_factory,
    mocked_jira,
    action_params_factory,
    webhook_event_change_factory,
):
    action_context = action_context_factory(
        operation=Operation.CREATE,
        current_step="maybe_update_issue_points",
        bug__see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
        jira__issue="JBI-234",
        bug__cf_fx_points="---",
    )

    params = action_params_factory(
        jira_project_key=action_context.jira.project,
    )
    steps.maybe_update_issue_points(
        action_context, parameters=params, jira_service=JiraService(mocked_jira)
    )

    mocked_jira.update_issue_field.assert_not_called()


def test_update_issue_points_missing_in_map(
    action_context_factory,
    mocked_jira,
    action_params_factory,
    webhook_event_change_factory,
    capturelogs,
):
    action_context = action_context_factory(
        operation=Operation.UPDATE,
        current_step="maybe_update_issue_points",
        bug__see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
        jira__issue="JBI-234",
        bug__cf_fx_points="42",
        event__action="modify",
        event__changes=[
            webhook_event_change_factory(field="cf_fx_points", removed="?", added="42")
        ],
    )

    params = action_params_factory(
        jira_project_key=action_context.jira.project,
    )

    with capturelogs.for_logger("jbi.steps").at_level(logging.DEBUG):
        result, _ = steps.maybe_update_issue_points(
            action_context, parameters=params, jira_service=JiraService(mocked_jira)
        )

    assert result == steps.StepStatus.INCOMPLETE
    mocked_jira.create_issue.assert_not_called()
    mocked_jira.update_issue_field.assert_not_called()
    assert capturelogs.messages == [
        "Bug cf_fx_points '42' was not in the cf_fx_points map."
    ]


@pytest.mark.parametrize(
    "project_components,bug_component,config_components,expected_jira_components,expected_logs",
    [
        # Default, only from bug.
        (
            # Jira project components
            [
                {
                    "id": "10000",
                    "name": "Component 1",
                },
                {
                    "id": "42",
                    "name": "Toolbar",
                },
            ],
            # Bug component
            "Toolbar",
            # Config
            JiraComponents(),
            # Expected issue components
            [{"id": "42"}],
            # Expected logs
            [],
        ),
        # Only from config.
        (
            # Jira project components
            [
                {
                    "id": "10000",
                    "name": "Component 1",
                },
                {
                    "id": "42",
                    "name": "Remote Settings",
                },
            ],
            # Bug component
            "Toolbar",
            # Config
            JiraComponents(
                use_bug_component=False, set_custom_components=["Remote Settings"]
            ),
            # Expected issue components
            [{"id": "42"}],
            # Expected logs
            [],
        ),
        # Using bug product.
        (
            # Jira project components
            [
                {
                    "id": "10000",
                    "name": "JBI",
                },
                {
                    "id": "20000",
                    "name": "Framework",
                },
            ],
            # Bug component
            "Framework",
            JiraComponents(use_bug_product=True),
            # Expected issue components
            [{"id": "10000"}, {"id": "20000"}],
            # Expected logs
            [],
        ),
        # Using bug prefixed component.
        (
            # Jira project components
            [
                {
                    "id": "10000",
                    "name": "JBI::",
                },
                {
                    "id": "20000",
                    "name": "General",
                },
            ],
            # Bug component
            None,
            # Config
            JiraComponents(use_bug_component_with_product_prefix=True),
            # Expected issue components
            [{"id": "10000"}],
            # Expected logs
            [],
        ),
        # Using bug prefixed component.
        (
            # Jira project components
            [
                {
                    "id": "10000",
                    "name": "JBI::General",
                },
            ],
            # Bug component
            "General",
            # Config
            JiraComponents(use_bug_component_with_product_prefix=True),
            # Expected issue components
            [{"id": "10000"}],
            # Expected logs
            ["Could not find components 'General' in project"],
        ),
        # Without components in project
        (
            # Jira project component
            [],
            # Bug component
            "Toolbar",
            # Config
            JiraComponents(),
            # Expected issue components
            [],
            # Expected logs
            ["Could not find components 'Toolbar' in project"],
        ),
        # With more than one in config
        (
            # Jira project component
            [
                {
                    "id": "10000",
                    "name": "Search",
                },
                {
                    "id": "42",
                    "name": "Remote Settings",
                },
            ],
            # Bug component
            None,
            # Config
            JiraComponents(set_custom_components=["Search", "Remote Settings"]),
            # Expected issue components
            [{"id": "10000"}, {"id": "42"}],
            # Expected logs
            [],
        ),
    ],
)
def test_maybe_update_components(
    project_components,
    bug_component,
    config_components,
    expected_jira_components,
    expected_logs,
    action_context_factory,
    mocked_jira,
    capturelogs,
    action_params_factory,
):
    mocked_jira.get_project_components.return_value = project_components

    action_context = action_context_factory(
        current_step="maybe_update_components",
        bug__component=bug_component,
        jira__issue="JBI-123",
    )
    action_params = action_params_factory(
        jira_project_key=action_context.jira.project,
        jira_components=config_components,
    )

    with capturelogs.for_logger("jbi.steps").at_level(logging.DEBUG):
        steps.maybe_update_components(
            context=action_context,
            parameters=action_params,
            jira_service=JiraService(mocked_jira),
        )

    if expected_jira_components:
        mocked_jira.update_issue_field.assert_called_with(
            key=action_context.jira.issue,
            fields={"components": expected_jira_components},
        )
    assert capturelogs.messages == expected_logs


def test_maybe_update_components_raises_incompletesteperror_on_mismatch(
    action_context_factory, mocked_jira, action_params_factory
):
    action_context = action_context_factory(
        operation=Operation.UPDATE,
        bug__see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
        bug__component="Frontend",
        jira__issue="JBI-234",
    )
    action_params = action_params_factory(
        jira_project_key=action_context.jira.project,
    )
    mocked_jira.get_project_components.return_value = [{"name": "Backend"}]

    result, _context = steps.maybe_update_components(
        action_context,
        parameters=action_params,
        jira_service=JiraService(mocked_jira),
    )
    assert result == steps.StepStatus.INCOMPLETE


def test_maybe_update_components_failing(
    action_context_factory,
    mocked_jira,
    capturelogs,
    action_params_factory,
):
    action_context = action_context_factory(
        operation=Operation.UPDATE,
        current_step="maybe_update_components",
        bug__see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
        bug__component="Frontend",
        jira__issue="JBI-234",
    )
    mocked_jira.get_project_components.return_value = [
        {"id": 1, "name": action_context.bug.component},
        {"id": 2, "name": action_context.bug.product_component},
    ]
    mocked_jira.update_issue_field.side_effect = requests.exceptions.HTTPError(
        "Field 'components' cannot be set", response=mock.MagicMock(status_code=400)
    )

    action_params = action_params_factory(jira_project_key=action_context.jira.project)

    with capturelogs.for_logger("jbi.steps").at_level(logging.DEBUG):
        result, _context = steps.maybe_update_components(
            context=action_context,
            parameters=action_params,
            jira_service=JiraService(mocked_jira),
        )
        assert result == steps.StepStatus.INCOMPLETE

    assert capturelogs.messages == [
        "Could not set components on issue JBI-234: Field 'components' cannot be set"
    ]


def test_sync_whiteboard_labels(
    action_context_factory,
    mocked_jira,
    action_params_factory,
):
    action_context = action_context_factory(
        operation=Operation.CREATE, jira__issue="JBI-123"
    )

    callable_object = Executor(
        action_params_factory(
            jira_project_key=action_context.jira.project,
            steps={"new": ["sync_whiteboard_labels"]},
        )
    )
    callable_object(context=action_context)

    mocked_jira.update_issue.assert_called_once_with(
        issue_key=action_context.jira.issue,
        update={
            "update": {
                "labels": [
                    {"add": "bugzilla"},
                    {"add": "devtest"},
                ]
            }
        },
    )


def test_sync_whiteboard_labels_with_brackets(
    action_context_factory, mocked_jira, action_params_factory
):
    action_context = action_context_factory(
        operation=Operation.CREATE, jira__issue="JBI-123"
    )
    callable_object = Executor(
        action_params_factory(
            jira_project_key=action_context.jira.project,
            steps={"new": ["sync_whiteboard_labels"]},
            labels_brackets="both",
        )
    )
    callable_object(context=action_context)

    mocked_jira.update_issue.assert_called_once_with(
        issue_key=action_context.jira.issue,
        update={
            "update": {
                "labels": [
                    {"add": "bugzilla"},
                    {"add": "devtest"},
                    {"add": "[devtest]"},
                ]
            }
        },
    )


def test_sync_whiteboard_labels_update(
    action_context_factory,
    mocked_jira,
    action_params_factory,
    webhook_event_change_factory,
):
    action_context = action_context_factory(
        operation=Operation.UPDATE,
        jira__issue="JBI-123",
        event__changes=[
            webhook_event_change_factory(
                field="whiteboard",
                removed="[remotesettings] [server]",
                added="[remotesettings]",
            )
        ],
    )

    callable_object = Executor(
        action_params_factory(
            jira_project_key=action_context.jira.project,
            steps={"existing": ["sync_whiteboard_labels"]},
        )
    )
    callable_object(context=action_context)

    mocked_jira.update_issue.assert_called_once_with(
        issue_key=action_context.jira.issue,
        update={
            "update": {
                "labels": [
                    {"add": "bugzilla"},
                    {"add": "remotesettings"},
                    {"remove": "server"},
                ]
            }
        },
    )


def test_sync_whiteboard_labels_failing(
    action_context_factory,
    mocked_jira,
    capturelogs,
    action_params_factory,
):
    mocked_jira.update_issue.side_effect = requests.exceptions.HTTPError(
        "some message", response=mock.MagicMock(status_code=400)
    )
    action_context = action_context_factory(
        current_step="sync_whiteboard_labels", jira__issue="JBI-123"
    )

    action_params = action_params_factory(
        jira_project_key=action_context.jira.project,
    )

    with capturelogs.for_logger("jbi.steps").at_level(logging.DEBUG):
        result, context = steps.sync_whiteboard_labels(
            context=action_context,
            parameters=action_params,
            jira_service=JiraService(mocked_jira),
        )
    assert result == steps.StepStatus.INCOMPLETE

    assert capturelogs.messages == [
        "Could not set labels on issue JBI-123: some message"
    ]


def test_sync_keywords_labels(
    action_context_factory,
    mocked_jira,
    action_params_factory,
):
    action_context = action_context_factory(
        operation=Operation.CREATE,
        jira__issue="JBI-123",
        bug__keywords=["devtests", "bugzilla"],
    )

    callable_object = Executor(
        action_params_factory(
            jira_project_key=action_context.jira.project,
            steps={"new": ["sync_whiteboard_labels"]},
        )
    )
    callable_object(context=action_context)

    mocked_jira.update_issue.assert_called_once_with(
        issue_key=action_context.jira.issue,
        update={
            "update": {
                "labels": [
                    {"add": "bugzilla"},
                    {"add": "devtest"},
                ]
            }
        },
    )


def test_sync_keywords_labels_update(
    action_context_factory,
    mocked_jira,
    action_params_factory,
    webhook_event_change_factory,
):
    action_context = action_context_factory(
        operation=Operation.UPDATE,
        jira__issue="JBI-123",
        event__changes=[
            webhook_event_change_factory(
                field="keywords",
                removed="interface, sprint, next",
                added="sprint",
            )
        ],
    )

    callable_object = Executor(
        action_params_factory(
            jira_project_key=action_context.jira.project,
            steps={"existing": ["sync_keywords_labels"]},
        )
    )
    callable_object(context=action_context)

    mocked_jira.update_issue.assert_called_once_with(
        issue_key=action_context.jira.issue,
        update={
            "update": {
                "labels": [
                    {"add": "sprint"},
                    {"remove": "interface"},
                    {"remove": "next"},
                ]
            }
        },
    )


def test_sync_keywords_labels_failing(
    action_context_factory,
    mocked_jira,
    capturelogs,
    action_params_factory,
):
    mocked_jira.update_issue.side_effect = requests.exceptions.HTTPError(
        "some message", response=mock.MagicMock(status_code=400)
    )
    action_context = action_context_factory(
        current_step="sync_keywords_labels", jira__issue="JBI-123"
    )

    action_params = action_params_factory(
        jira_project_key=action_context.jira.project,
    )

    with capturelogs.for_logger("jbi.steps").at_level(logging.DEBUG):
        result, context = steps.sync_keywords_labels(
            context=action_context,
            parameters=action_params,
            jira_service=JiraService(mocked_jira),
        )
    assert result == steps.StepStatus.INCOMPLETE

    assert capturelogs.messages == [
        "Could not set labels on issue JBI-123: some message"
    ]
