"""
Module for testing jbi/actions/default.py functionality
"""
import logging
from unittest import mock

import pytest
import requests

from jbi.actions import default
from jbi.environment import get_settings
from jbi.models import ActionContext
from jbi.services.jira import JiraCreateError
from tests.fixtures.factories import comment_factory, webhook_event_change_factory

ALL_STEPS = {
    "new": [
        "create_issue",
        "maybe_delete_duplicate",
        "add_link_to_bugzilla",
        "add_link_to_jira",
        "maybe_assign_jira_user",
        "maybe_update_issue_resolution",
        "maybe_update_issue_status",
    ],
    "existing": [
        "update_issue_summary",
        "maybe_assign_jira_user",
        "maybe_update_issue_resolution",
        "maybe_update_issue_status",
    ],
    "comment": [
        "create_comment",
    ],
}


def test_created_public(
    context_create_example: ActionContext, mocked_jira, mocked_bugzilla
):
    mocked_jira.create_issue.return_value = {"key": "k"}
    mocked_bugzilla.get_bug.return_value = context_create_example.bug
    mocked_bugzilla.get_comments.return_value = [
        comment_factory(text="Initial comment")
    ]
    callable_object = default.init(jira_project_key=context_create_example.jira.project)

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
        654321, see_also={"add": [f"{get_settings().jira_base_url}browse/k"]}
    )


def test_modified_public(context_update_example: ActionContext, mocked_jira):
    context_update_example.event.changes = [
        webhook_event_change_factory(field="summary", removed="", added="JBI Test")
    ]

    callable_object = default.init(jira_project_key=context_update_example.jira.project)

    callable_object(context=context_update_example)

    assert context_update_example.bug.extract_from_see_also(), "see_also is not empty"

    mocked_jira.update_issue_field.assert_called_once_with(
        key="JBI-234",
        fields={"summary": "JBI Test"},
    )


def test_comment_for_modified_assignee_and_status(
    context_update_status_assignee: ActionContext, mocked_jira
):
    callable_object = default.init(
        jira_project_key=context_update_status_assignee.jira.project
    )

    callable_object(context=context_update_status_assignee)

    mocked_jira.issue_add_comment.assert_any_call(
        issue_key="JBI-234",
        comment='{\n    "assignee": "nobody@mozilla.org"\n}',
    )
    mocked_jira.issue_add_comment.assert_any_call(
        issue_key="JBI-234",
        comment='{\n    "modified by": "nobody@mozilla.org",\n    "resolution": "",\n    "status": "NEW"\n}',
    )


def test_added_comment(context_comment_example: ActionContext, mocked_jira):
    callable_object = default.init(
        jira_project_key=context_comment_example.jira.project
    )

    callable_object(context=context_comment_example)

    mocked_jira.issue_add_comment.assert_called_once_with(
        issue_key="JBI-234",
        comment="*(mathieu@mozilla.org)* commented: \n{quote}hello{quote}",
    )


def test_jira_returns_an_error(context_create_example: ActionContext, mocked_jira):
    mocked_jira.create_issue.return_value = [
        {"errors": ["Boom"]},
    ]
    callable_object = default.init(jira_project_key=context_create_example.jira.project)

    with pytest.raises(JiraCreateError) as exc_info:
        callable_object(context=context_create_example)

    assert str(exc_info.value) == "Boom"


def test_create_with_no_assignee(
    context_create_example: ActionContext, mocked_jira, mocked_bugzilla
):
    mocked_bugzilla.get_bug.return_value = context_create_example.bug
    mocked_bugzilla.get_comments.return_value = [
        comment_factory(text="Initial comment")
    ]
    mocked_jira.create_issue.return_value = {"key": "new-id"}
    callable_object = default.init(
        jira_project_key=context_create_example.jira.project, steps=ALL_STEPS
    )
    handled, _ = callable_object(context=context_create_example)

    assert handled
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


def test_create_with_assignee(
    context_create_example: ActionContext, mocked_jira, mocked_bugzilla
):
    context_create_example.bug.assigned_to = "dtownsend@mozilla.com"
    # Make sure the bug fetched the second time in `create_and_link_issue()` also has the assignee.
    mocked_bugzilla.get_bug.return_value = context_create_example.bug
    mocked_jira.create_issue.return_value = {"key": "JBI-534"}
    mocked_jira.user_find_by_user_string.return_value = [{"accountId": "6254"}]
    mocked_bugzilla.get_comments.return_value = [
        comment_factory(text="Initial comment")
    ]

    callable_object = default.init(
        jira_project_key=context_create_example.jira.project, steps=ALL_STEPS
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
    mocked_jira.user_find_by_user_string.assert_called_once_with(
        query="dtownsend@mozilla.com"
    )
    mocked_jira.update_issue_field.assert_called_once_with(
        key="JBI-534",
        fields={"assignee": {"accountId": "6254"}},
    )
    mocked_jira.set_issue_status.assert_not_called()


def test_clear_assignee(context_update_example: ActionContext, mocked_jira):
    context_update_example.event.action = "modify"
    context_update_example.event.routing_key = "bug.modify:assigned_to"

    callable_object = default.init(
        jira_project_key=context_update_example.jira.project, steps=ALL_STEPS
    )
    callable_object(context=context_update_example)

    mocked_jira.create_issue.assert_not_called()
    mocked_jira.user_find_by_user_string.assert_not_called()
    mocked_jira.update_issue_field.assert_any_call(
        key="JBI-234",
        fields={"assignee": None},
    )
    mocked_jira.set_issue_status.assert_not_called()


def test_set_assignee(context_update_example: ActionContext, mocked_jira):
    context_update_example.bug.assigned_to = "dtownsend@mozilla.com"
    context_update_example.event.action = "modify"
    context_update_example.event.routing_key = "bug.modify:assigned_to"

    mocked_jira.user_find_by_user_string.return_value = [{"accountId": "6254"}]

    callable_object = default.init(
        jira_project_key=context_update_example.jira.project, steps=ALL_STEPS
    )
    callable_object(context=context_update_example)

    mocked_jira.create_issue.assert_not_called()
    mocked_jira.user_find_by_user_string.assert_called_once_with(
        query="dtownsend@mozilla.com"
    )
    mocked_jira.update_issue_field.assert_any_call(
        key="JBI-234",
        fields={"assignee": {"accountId": "6254"}},
    )
    mocked_jira.set_issue_status.assert_not_called()


def test_create_with_unknown_status(
    context_create_example: ActionContext, mocked_jira, mocked_bugzilla
):
    context_create_example.bug.status = "NEW"
    context_create_example.bug.resolution = ""
    mocked_bugzilla.get_bug.return_value = context_create_example.bug
    mocked_bugzilla.get_comments.return_value = [
        comment_factory(text="Initial comment")
    ]
    mocked_jira.create_issue.return_value = {"key": "new-id"}

    callable_object = default.init(
        jira_project_key=context_create_example.jira.project,
        steps=ALL_STEPS,
        status_map={
            "ASSIGNED": "In Progress",
            "FIXED": "Closed",
        },
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
    mocked_jira.user_find_by_user_string.assert_not_called()
    mocked_jira.update_issue_field.assert_not_called()
    mocked_jira.set_issue_status.assert_not_called()


def test_create_with_known_status(
    context_create_example: ActionContext, mocked_jira, mocked_bugzilla
):
    context_create_example.bug.status = "ASSIGNED"
    context_create_example.bug.resolution = ""
    # Make sure the bug fetched the second time in `create_and_link_issue()` also has the status.
    mocked_bugzilla.get_bug.return_value = context_create_example.bug
    mocked_bugzilla.get_comments.return_value = [
        comment_factory(text="Initial comment")
    ]
    mocked_jira.create_issue.return_value = {"key": "JBI-534"}

    callable_object = default.init(
        jira_project_key=context_create_example.jira.project,
        steps=ALL_STEPS,
        status_map={
            "ASSIGNED": "In Progress",
            "FIXED": "Closed",
        },
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
    mocked_jira.user_find_by_user_string.assert_not_called()
    mocked_jira.update_issue_field.assert_not_called()
    mocked_jira.set_issue_status.assert_called_once_with("JBI-534", "In Progress")


def test_change_to_unknown_status(context_update_example: ActionContext, mocked_jira):
    context_update_example.bug.status = "NEW"
    context_update_example.bug.resolution = ""
    context_update_example.event.action = "modify"
    context_update_example.event.routing_key = "bug.modify:status"

    callable_object = default.init(
        jira_project_key=context_update_example.jira.project,
        steps=ALL_STEPS,
        status_map={
            "ASSIGNED": "In Progress",
            "FIXED": "Closed",
        },
    )
    callable_object(context=context_update_example)

    mocked_jira.create_issue.assert_not_called()
    mocked_jira.user_find_by_user_string.assert_not_called()
    mocked_jira.set_issue_status.assert_not_called()


def test_change_to_known_status(context_update_example: ActionContext, mocked_jira):
    context_update_example.bug.status = "ASSIGNED"
    context_update_example.bug.resolution = ""
    context_update_example.event.action = "modify"
    context_update_example.event.routing_key = "bug.modify:status"

    callable_object = default.init(
        jira_project_key=context_update_example.jira.project,
        steps=ALL_STEPS,
        status_map={
            "ASSIGNED": "In Progress",
            "FIXED": "Closed",
        },
    )
    callable_object(context=context_update_example)

    mocked_jira.create_issue.assert_not_called()
    mocked_jira.user_find_by_user_string.assert_not_called()
    mocked_jira.set_issue_status.assert_called_once_with("JBI-234", "In Progress")


def test_change_to_known_resolution(context_update_example: ActionContext, mocked_jira):
    context_update_example.bug.status = "RESOLVED"
    context_update_example.bug.resolution = "FIXED"
    context_update_example.event.action = "modify"
    context_update_example.event.routing_key = "bug.modify:resolution"

    callable_object = default.init(
        jira_project_key=context_update_example.jira.project,
        steps=ALL_STEPS,
        status_map={
            "ASSIGNED": "In Progress",
            "FIXED": "Closed",
        },
    )
    callable_object(context=context_update_example)

    mocked_jira.create_issue.assert_not_called()
    mocked_jira.user_find_by_user_string.assert_not_called()
    mocked_jira.set_issue_status.assert_called_once_with("JBI-234", "Closed")


def test_change_to_known_resolution_with_resolution_map(
    context_update_resolution_example: ActionContext, mocked_jira
):
    context_update_resolution_example.bug.resolution = "DUPLICATE"

    callable_object = default.init(
        jira_project_key=context_update_resolution_example.jira.project,
        steps=ALL_STEPS,
        resolution_map={
            "DUPLICATE": "Duplicate",
        },
    )
    callable_object(context=context_update_resolution_example)

    mocked_jira.update_issue_field.assert_called_with(  # not once
        key="JBI-234",
        fields={
            "resolution": "Duplicate",
        },
    )


def test_change_to_unknown_resolution_with_resolution_map(
    context_update_resolution_example: ActionContext, mocked_jira
):
    context_update_resolution_example.bug.resolution = "WONTFIX"

    callable_object = default.init(
        jira_project_key=context_update_resolution_example.jira.project,
        steps=ALL_STEPS,
        resolution_map={
            "DUPLICATE": "Duplicate",
        },
    )
    callable_object(context=context_update_resolution_example)

    mocked_jira.update_issue_field.assert_not_called()


@pytest.mark.parametrize(
    "project_components,bug_component,config_components,expected_jira_components,expected_logs",
    [
        (
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
            "Toolbar",
            ["Remote Settings"],
            [{"id": "42"}],
            ["Could not find components {'Toolbar'} in project"],
        ),
        # Without components in config
        (
            [
                {
                    "id": "37",
                    "name": "Toolbar",
                },
            ],
            "Toolbar",
            [],
            [{"id": "37"}],
            [],
        ),
        # Without components in project
        (
            [],
            "Toolbar",
            [],
            [],
            ["Could not find components {'Toolbar'} in project"],
        ),
        # With more than one in config
        (
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
            None,
            ["Search", "Remote Settings"],
            [{"id": "10000"}, {"id": "42"}],
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
    context_create_example,
    mocked_jira,
    caplog,
):
    mocked_jira.get_project_components.return_value = project_components
    context_create_example.bug.component = bug_component

    callable_object = default.init(
        jira_project_key=context_create_example.jira.project,
        steps={"new": ["maybe_update_components"]},
        jira_components=config_components,
    )

    with caplog.at_level(logging.DEBUG):
        callable_object(context=context_create_example)

    captured_log_msgs = [
        r.msg % r.args for r in caplog.records if r.name == "jbi.actions.steps"
    ]
    if expected_jira_components:
        mocked_jira.update_issue_field.assert_called_with(
            key=context_create_example.jira.issue,
            fields={"components": expected_jira_components},
        )
    assert captured_log_msgs == expected_logs


def test_sync_whiteboard_labels(context_create_example: ActionContext, mocked_jira):
    callable_object = default.init(
        jira_project_key=context_create_example.jira.project,
        steps={"new": ["sync_whiteboard_labels"]},
    )
    callable_object(context=context_create_example)

    mocked_jira.update_issue.assert_called_once_with(
        issue_key=context_create_example.jira.issue,
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


def test_sync_whiteboard_labels_without_brackets(
    context_create_example: ActionContext, mocked_jira
):
    callable_object = default.init(
        jira_project_key=context_create_example.jira.project,
        steps={"new": ["sync_whiteboard_labels"]},
        labels_brackets="no",
    )
    callable_object(context=context_create_example)

    mocked_jira.update_issue.assert_called_once_with(
        issue_key=context_create_example.jira.issue,
        update={
            "update": {
                "labels": [
                    {"add": "bugzilla"},
                    {"add": "devtest"},
                ]
            }
        },
    )


def test_sync_whiteboard_labels_update(
    context_update_example: ActionContext, mocked_jira
):
    context_update_example.event.changes = [
        webhook_event_change_factory(
            field="whiteboard",
            removed="[remotesettings] [server]",
            added="[remotesettings]",
        )
    ]

    callable_object = default.init(
        jira_project_key=context_update_example.jira.project,
        steps={"existing": ["sync_whiteboard_labels"]},
    )
    callable_object(context=context_update_example)

    mocked_jira.update_issue.assert_called_once_with(
        issue_key=context_update_example.jira.issue,
        update={
            "update": {
                "labels": [
                    {"add": "bugzilla"},
                    {"add": "remotesettings"},
                    {"add": "[remotesettings]"},
                    {"remove": "[server]"},
                    {"remove": "server"},
                ]
            }
        },
    )


def test_sync_whiteboard_labels_failing(
    context_update_example: ActionContext, mocked_jira, caplog
):
    mocked_jira.update_issue.side_effect = requests.exceptions.HTTPError(
        "some message", response=mock.MagicMock(status_code=400)
    )

    callable_object = default.init(
        jira_project_key=context_update_example.jira.project,
        steps={"existing": ["sync_whiteboard_labels"]},
    )

    with caplog.at_level(logging.DEBUG):
        callable_object(context=context_update_example)

    captured_log_msgs = [
        r.msg % r.args for r in caplog.records if r.name == "jbi.actions.steps"
    ]
    assert captured_log_msgs == ["Could not set labels on issue JBI-234: some message"]
