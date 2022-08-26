from jbi.actions import default_with_assignee_and_status as action
from tests.fixtures.factories import comment_factory


def test_create_with_no_assignee(webhook_create_example, mocked_jira, mocked_bugzilla):
    mocked_bugzilla.get_bug.return_value = webhook_create_example.bug
    mocked_bugzilla.get_comments.return_value = [
        comment_factory(text="Initial comment")
    ]
    mocked_jira.create_issue.return_value = {"key": "new-id"}
    callable_object = action.init(jira_project_key="JBI")
    handled, _ = callable_object(payload=webhook_create_example)

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


def test_create_with_assignee(webhook_create_example, mocked_jira, mocked_bugzilla):
    webhook_create_example.bug.assigned_to = "dtownsend@mozilla.com"
    # Make sure the bug fetched the second time in `create_and_link_issue()` also has the assignee.
    mocked_bugzilla.get_bug.return_value = webhook_create_example.bug
    mocked_jira.create_issue.return_value = {"key": "JBI-534"}
    mocked_jira.user_find_by_user_string.return_value = [{"accountId": "6254"}]
    mocked_bugzilla.get_comments.return_value = [
        comment_factory(text="Initial comment")
    ]

    callable_object = action.init(jira_project_key="JBI")
    callable_object(payload=webhook_create_example)

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


def test_clear_assignee(webhook_create_example, mocked_jira):
    webhook_create_example.bug.see_also = [
        "https://mozilla.atlassian.net/browse/JBI-234"
    ]
    webhook_create_example.event.action = "modify"
    webhook_create_example.event.routing_key = "bug.modify:assigned_to"

    callable_object = action.init(jira_project_key="JBI")
    callable_object(payload=webhook_create_example)

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


def test_set_assignee(webhook_create_example, mocked_jira):
    webhook_create_example.bug.assigned_to = "dtownsend@mozilla.com"
    webhook_create_example.bug.see_also = [
        "https://mozilla.atlassian.net/browse/JBI-234"
    ]
    webhook_create_example.event.action = "modify"
    webhook_create_example.event.routing_key = "bug.modify:assigned_to"

    mocked_jira.user_find_by_user_string.return_value = [{"accountId": "6254"}]

    callable_object = action.init(jira_project_key="JBI")
    callable_object(payload=webhook_create_example)

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
    webhook_create_example, mocked_jira, mocked_bugzilla
):
    webhook_create_example.bug.status = "NEW"
    webhook_create_example.bug.resolution = ""
    mocked_bugzilla.get_bug.return_value = webhook_create_example.bug
    mocked_bugzilla.get_comments.return_value = [
        comment_factory(text="Initial comment")
    ]
    mocked_jira.create_issue.return_value = {"key": "new-id"}

    callable_object = action.init(
        jira_project_key="JBI",
        status_map={
            "ASSIGNED": "In Progress",
            "FIXED": "Closed",
        },
    )
    callable_object(payload=webhook_create_example)

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


def test_create_with_known_status(webhook_create_example, mocked_jira, mocked_bugzilla):
    webhook_create_example.bug.status = "ASSIGNED"
    webhook_create_example.bug.resolution = ""
    # Make sure the bug fetched the second time in `create_and_link_issue()` also has the status.
    mocked_bugzilla.get_bug.return_value = webhook_create_example.bug
    mocked_bugzilla.get_comments.return_value = [
        comment_factory(text="Initial comment")
    ]
    mocked_jira.create_issue.return_value = {"key": "JBI-534"}

    callable_object = action.init(
        jira_project_key="JBI",
        status_map={
            "ASSIGNED": "In Progress",
            "FIXED": "Closed",
        },
    )
    callable_object(payload=webhook_create_example)

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


def test_change_to_unknown_status(webhook_create_example, mocked_jira):
    webhook_create_example.bug.status = "NEW"
    webhook_create_example.bug.resolution = ""
    webhook_create_example.bug.see_also = [
        "https://mozilla.atlassian.net/browse/JBI-234"
    ]
    webhook_create_example.event.action = "modify"
    webhook_create_example.event.routing_key = "bug.modify:status"

    callable_object = action.init(
        jira_project_key="JBI",
        status_map={
            "ASSIGNED": "In Progress",
            "FIXED": "Closed",
        },
    )
    callable_object(payload=webhook_create_example)

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


def test_change_to_known_status(webhook_create_example, mocked_jira):
    webhook_create_example.bug.status = "ASSIGNED"
    webhook_create_example.bug.resolution = ""
    webhook_create_example.bug.see_also = [
        "https://mozilla.atlassian.net/browse/JBI-234"
    ]
    webhook_create_example.event.action = "modify"
    webhook_create_example.event.routing_key = "bug.modify:status"

    callable_object = action.init(
        jira_project_key="JBI",
        status_map={
            "ASSIGNED": "In Progress",
            "FIXED": "Closed",
        },
    )
    callable_object(payload=webhook_create_example)

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


def test_change_to_known_resolution(webhook_create_example, mocked_jira):
    webhook_create_example.bug.status = "RESOLVED"
    webhook_create_example.bug.resolution = "FIXED"
    webhook_create_example.bug.see_also = [
        "https://mozilla.atlassian.net/browse/JBI-234"
    ]
    webhook_create_example.event.action = "modify"
    webhook_create_example.event.routing_key = "bug.modify:resolution"

    callable_object = action.init(
        jira_project_key="JBI",
        status_map={
            "ASSIGNED": "In Progress",
            "FIXED": "Closed",
        },
    )
    callable_object(payload=webhook_create_example)

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
