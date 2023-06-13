import pytest

from jbi.errors import ActionNotFoundError
from jbi.models import Actions
from tests.fixtures.factories import bug_factory


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


@pytest.mark.parametrize(
    "whiteboard",
    [
        "[DevTest-]",
        "[-DevTest-]",
        "[-DevTest]",
        "[DevTest-test]",
        "[test-DevTest]",
        "[foo-DevTest-bar]",
        "[foo-bar-DevTest-foo-bar]",
        "[example][DevTest]",
        "[DevTest][example]",
        "[example][DevTest][example]",
    ],
)
def test_lookup_action_found(whiteboard, actions_example):
    bug = bug_factory(id=1234, whiteboard=whiteboard)
    action = bug.lookup_action(actions_example)
    assert action.whiteboard_tag == "devtest"
    assert "test config" in action.description


@pytest.mark.parametrize(
    "whiteboard",
    [
        "DevTest",
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
def test_lookup_action_not_found(whiteboard, actions_example):
    bug = bug_factory(id=1234, whiteboard=whiteboard)
    with pytest.raises(ActionNotFoundError) as exc_info:
        bug.lookup_action(actions_example)
    assert str(exc_info.value) == "devtest"


def test_payload_empty_changes_list(webhook_change_status_assignee):
    webhook_change_status_assignee.event.changes = None
    assert webhook_change_status_assignee.event.changed_fields() == []


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
