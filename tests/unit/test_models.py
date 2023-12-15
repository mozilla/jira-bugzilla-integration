import pydantic
import pytest

from jbi.errors import ActionNotFoundError
from jbi.models import ActionParams, Actions, ActionSteps


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
        Actions(root=[])
    assert "List should have at least 1 item after validation, not 0" in str(
        exc_info.value
    )


def test_default_invalid_step():
    with pytest.raises(pydantic.ValidationError) as exc:
        ActionSteps(new=["BOOM", "POW"], comment=["BAM"])
    error_message = str(exc.value)

    assert "BOOM" in error_message
    assert "POW" in error_message
    assert "BAM" in error_message


def test_duplicated_whiteboard_tag_fails(action_factory):
    with pytest.raises(ValueError) as exc_info:
        Actions(
            root=[
                action_factory(whiteboard_tag="x"),
                action_factory(whiteboard_tag="y"),
                action_factory(whiteboard_tag="x"),
            ]
        )
    assert "actions have duplicated lookup tags: ['x']" in str(exc_info.value)


def test_override_step_configuration_for_single_action_type():
    default_steps = ActionSteps()
    params = ActionParams(
        jira_project_key="JBI", steps=ActionSteps(new=["create_issue"])
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
        (
            ["http://mozilla.jira.com/FOO-123", "http://mozilla.jira.com/BAR-456"],
            "FOO-123",
        ),
        (
            ["http://mozilla.jira.com/FOO-123", "http://mozilla.jira.com/JBI456"],
            "FOO-123",
        ),
        (
            ["http://mozilla.jira.com/FOO-123", "http://mozilla.jira.com/JBI-456"],
            "JBI-456",
        ),
    ],
)
def test_extract_see_also(see_also, expected, bug_factory):
    bug = bug_factory(see_also=see_also)
    assert bug.extract_from_see_also("JBI") == expected


@pytest.mark.parametrize(
    "product,component,expected",
    [
        (None, None, ""),
        (None, "General", "General"),
        ("Product", None, "Product::"),
        ("Product", "General", "Product::General"),
    ],
)
def test_product_component(product, component, expected, bug_factory):
    bug = bug_factory(product=product, component=component)
    assert bug.product_component == expected


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
def test_lookup_action_found(whiteboard, actions_factory, bug_factory):
    bug = bug_factory(id=1234, whiteboard=whiteboard)
    action = bug.lookup_action(actions_factory())
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
def test_lookup_action_not_found(whiteboard, actions_factory, bug_factory):
    bug = bug_factory(id=1234, whiteboard=whiteboard)
    with pytest.raises(ActionNotFoundError) as exc_info:
        bug.lookup_action(actions_factory())
    assert str(exc_info.value) == "devtest"


def test_payload_empty_changes_list(webhook_event_factory):
    event = webhook_event_factory(routing_key="bug.modify", changes=None)
    assert event.changed_fields() == []


def test_payload_changes_list(webhook_event_change_factory, webhook_event_factory):
    changes = [
        webhook_event_change_factory(field="status", removed="OPEN", added="FIXED"),
        webhook_event_change_factory(
            field="assignee", removed="nobody@mozilla.org", added="mathieu@mozilla.com"
        ),
    ]
    event = webhook_event_factory(routing_key="bug.modify", changes=changes)
    assert event.changed_fields() == [
        "status",
        "assignee",
    ]


def test_payload_changes_coerces_numbers_to_strings(
    webhook_event_change_factory, webhook_event_factory
):
    changes = [
        webhook_event_change_factory(field="is_confirmed", removed="1", added=0),
    ]
    event = webhook_event_factory(routing_key="bug.modify", changes=changes)
    assert event.changed_fields() == ["is_confirmed"]
    assert event.changes[0].added == "0"


def test_max_configured_projects_raises_error(action_factory):
    actions = [action_factory(whiteboard_tag=str(i)) for i in range(51)]
    with pytest.raises(pydantic.ValidationError):
        Actions(root=actions)
