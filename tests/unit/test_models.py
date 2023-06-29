import pydantic
import pytest

from jbi.errors import ActionNotFoundError
from jbi.models import ActionParams, Actions, ActionSteps
from tests.fixtures.factories import bug_factory


@pytest.mark.parametrize("value", [123456, [123456], [12345, 67890], "tbd"])
def test_valid_bugzilla_user_ids(action_factory, value):
    action = action_factory(bugzilla_user_id=value)
    assert action.bugzilla_user_id == value


@pytest.mark.parametrize("value", [None, "foobar@example.com"])
def test_invalid_bugzilla_user_ids(action_factory, value):
    with pytest.raises(pydantic.ValidationError):
        action_factory(bugzilla_user_id=value)


def test_no_actions_fails():
    with pytest.raises(ValueError) as exc_info:
        Actions.parse_obj([])
    assert "ensure this value has at least 1 items" in str(exc_info.value)


def test_duplicated_whiteboard_tag_fails(action_factory):
    with pytest.raises(ValueError) as exc_info:
        Actions.parse_obj(
            [
                action_factory(whiteboard_tag="x"),
                action_factory(whiteboard_tag="y"),
                action_factory(whiteboard_tag="x"),
            ]
        )
    assert "actions have duplicated lookup tags: ['x']" in str(exc_info.value)


def test_override_step_configuration_for_single_action_type():
    default_steps = ActionSteps()
    params = ActionParams.parse_obj(
        {"jira_project_key": "JBI", "steps": {"new": ["create_issue"]}}
    )
    assert params.steps.new == ["create_issue"]
    assert params.steps.new != default_steps.new
    assert params.steps.existing == default_steps.existing
    assert params.steps.comment == default_steps.comment


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
        "[DevTest]",
        "[DevTest-]",
        "[DevTest-test]",
        "[DevTest-test-foo]",
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
