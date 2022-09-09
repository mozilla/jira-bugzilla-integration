from jbi.actions import default_with_assignee_and_status as action
from jbi.models import ActionContext
from tests.fixtures.factories import comment_factory


def test_create_with_no_assignee(
    context_create_example: ActionContext, mocked_jira, mocked_bugzilla
):
    mocked_bugzilla.get_bug.return_value = context_create_example.bug
    mocked_bugzilla.get_comments.return_value = [
        comment_factory(text="Initial comment")
    ]
    mocked_jira.create_issue.return_value = {"key": "new-id"}
    callable_object = action.init(jira_project_key=context_create_example.jira.project)
    handled, _ = callable_object(context=context_create_example)

    assert handled
    mocked_jira.create_issue.assert_called_once_with(
        fields={
            "summary": "JBI Test",
            "labels": ["bugzilla", "devtest", "[devtest]"],
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

    callable_object = action.init(jira_project_key=context_create_example.jira.project)
    callable_object(context=context_create_example)

    mocked_jira.create_issue.assert_called_once_with(
        fields={
            "summary": "JBI Test",
            "labels": ["bugzilla", "devtest", "[devtest]"],
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

    callable_object = action.init(jira_project_key=context_update_example.jira.project)
    callable_object(context=context_update_example)

    mocked_jira.create_issue.assert_not_called()
    mocked_jira.user_find_by_user_string.assert_not_called()
    mocked_jira.update_issue_field.assert_any_call(
        key="JBI-234",
        fields={
            "summary": "JBI Test",
            "labels": ["bugzilla", "devtest", "[devtest]"],
        },
    )
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

    callable_object = action.init(jira_project_key=context_update_example.jira.project)
    callable_object(context=context_update_example)

    mocked_jira.create_issue.assert_not_called()
    mocked_jira.user_find_by_user_string.assert_called_once_with(
        query="dtownsend@mozilla.com"
    )
    mocked_jira.update_issue_field.assert_any_call(
        key="JBI-234",
        fields={
            "summary": "JBI Test",
            "labels": ["bugzilla", "devtest", "[devtest]"],
        },
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

    callable_object = action.init(
        jira_project_key=context_create_example.jira.project,
        status_map={
            "ASSIGNED": "In Progress",
            "FIXED": "Closed",
        },
    )
    callable_object(context=context_create_example)

    mocked_jira.create_issue.assert_called_once_with(
        fields={
            "summary": "JBI Test",
            "labels": ["bugzilla", "devtest", "[devtest]"],
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

    callable_object = action.init(
        jira_project_key=context_create_example.jira.project,
        status_map={
            "ASSIGNED": "In Progress",
            "FIXED": "Closed",
        },
    )
    callable_object(context=context_create_example)

    mocked_jira.create_issue.assert_called_once_with(
        fields={
            "summary": "JBI Test",
            "labels": ["bugzilla", "devtest", "[devtest]"],
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

    callable_object = action.init(
        jira_project_key=context_update_example.jira.project,
        status_map={
            "ASSIGNED": "In Progress",
            "FIXED": "Closed",
        },
    )
    callable_object(context=context_update_example)

    mocked_jira.create_issue.assert_not_called()
    mocked_jira.user_find_by_user_string.assert_not_called()
    mocked_jira.update_issue_field.assert_called_once_with(
        key="JBI-234",
        fields={
            "summary": "JBI Test",
            "labels": ["bugzilla", "devtest", "[devtest]"],
        },
    )
    mocked_jira.set_issue_status.assert_not_called()


def test_change_to_known_status(context_update_example: ActionContext, mocked_jira):
    context_update_example.bug.status = "ASSIGNED"
    context_update_example.bug.resolution = ""
    context_update_example.event.action = "modify"
    context_update_example.event.routing_key = "bug.modify:status"

    callable_object = action.init(
        jira_project_key=context_update_example.jira.project,
        status_map={
            "ASSIGNED": "In Progress",
            "FIXED": "Closed",
        },
    )
    callable_object(context=context_update_example)

    mocked_jira.create_issue.assert_not_called()
    mocked_jira.user_find_by_user_string.assert_not_called()
    mocked_jira.update_issue_field.assert_called_once_with(
        key="JBI-234",
        fields={
            "summary": "JBI Test",
            "labels": ["bugzilla", "devtest", "[devtest]"],
        },
    )
    mocked_jira.set_issue_status.assert_called_once_with("JBI-234", "In Progress")


def test_change_to_known_resolution(context_update_example: ActionContext, mocked_jira):
    context_update_example.bug.status = "RESOLVED"
    context_update_example.bug.resolution = "FIXED"
    context_update_example.event.action = "modify"
    context_update_example.event.routing_key = "bug.modify:resolution"

    callable_object = action.init(
        jira_project_key=context_update_example.jira.project,
        status_map={
            "ASSIGNED": "In Progress",
            "FIXED": "Closed",
        },
    )
    callable_object(context=context_update_example)

    mocked_jira.create_issue.assert_not_called()
    mocked_jira.user_find_by_user_string.assert_not_called()
    mocked_jira.update_issue_field.assert_called_once_with(
        key="JBI-234",
        fields={
            "summary": "JBI Test",
            "labels": ["bugzilla", "devtest", "[devtest]"],
        },
    )
    mocked_jira.set_issue_status.assert_called_once_with("JBI-234", "Closed")


def test_change_to_known_resolution_with_resolution_map(
    context_update_resolution_example: ActionContext, mocked_jira
):
    context_update_resolution_example.bug.resolution = "DUPLICATE"

    callable_object = action.init(
        jira_project_key=context_update_resolution_example.jira.project,
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

    callable_object = action.init(
        jira_project_key=context_update_resolution_example.jira.project,
        resolution_map={
            "DUPLICATE": "Duplicate",
        },
    )
    callable_object(context=context_update_resolution_example)

    mocked_jira.update_issue_field.assert_called_once_with(
        key="JBI-234",
        fields={"summary": "JBI Test", "labels": ["bugzilla", "devtest", "[devtest]"]},
    )
