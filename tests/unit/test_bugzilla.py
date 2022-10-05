import pytest

from jbi.errors import ActionNotFoundError
from tests.fixtures.factories import bug_factory


@pytest.mark.parametrize(
    "whiteboard,expected",
    [
        ("", ["bugzilla"]),
        ("[test whiteboard]", ["bugzilla", "test.whiteboard", "[test.whiteboard]"]),
        ("[test-whiteboard]", ["bugzilla", "test-whiteboard", "[test-whiteboard]"]),
        (
            "[test whiteboard][test-no-space][test-both space-and-not",
            [
                "bugzilla",
                "test.whiteboard",
                "test-no-space",
                "test-both.space-and-not",
                "[test.whiteboard]",
                "[test-no-space]",
                "[test-both.space-and-not]",
            ],
        ),
    ],
)
def test_jira_labels(whiteboard, expected):
    bug = bug_factory(whiteboard=whiteboard)
    assert bug.get_jira_labels() == expected


@pytest.mark.parametrize(
    "see_also,expected",
    [
        (None, None),
        ([], None),
        (["foo"], None),
        (["fail:/format"], None),
        (["foo", "http://jira.net/123"], "123"),
        (["http://org/123"], None),
        (["http://jira.com"], None),
        (["http://mozilla.jira.com/"], None),
        (["http://mozilla.jira.com/123"], "123"),
        (["http://mozilla.jira.com/123/"], "123"),
        (["http://mozilla.jira.com/ticket/123"], "123"),
        (["http://atlassian.com/ticket/123"], "123"),
        (["http://mozilla.jira.com/123", "http://mozilla.jira.com/456"], "123"),
    ],
)
def test_extract_see_also(see_also, expected):
    bug = bug_factory(see_also=see_also)
    assert bug.extract_from_see_also() == expected


def test_lookup_action(actions_example):
    bug = bug_factory(id=1234, whiteboard="[example][DevTest]")
    action = bug.lookup_action(actions_example)
    assert action.whiteboard_tag == "devtest"
    assert "test config" in action.description


def test_lookup_action_missing(actions_example):
    bug = bug_factory(id=1234, whiteboard="example DevTest")
    with pytest.raises(ActionNotFoundError) as exc_info:
        bug.lookup_action(actions_example)
    assert str(exc_info.value) == "example devtest"


def test_payload_empty_changes_list(webhook_change_status_assignee):
    webhook_change_status_assignee.event.changes = None
    assert webhook_change_status_assignee.event.changed_fields() is None


def test_payload_changes_list(webhook_change_status_assignee):
    assert webhook_change_status_assignee.event.changed_fields() == [
        "status",
        "assignee",
    ]


def test_payload_changes_list_in_routing_key(webhook_change_status_assignee):
    webhook_change_status_assignee.event.changes = None
    webhook_change_status_assignee.event.routing_key = "bug.modify:assigned_to,status"

    assert webhook_change_status_assignee.event.changed_fields() == [
        "assigned_to",
        "status",
    ]
