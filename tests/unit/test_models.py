import pydantic
import pytest
from fastapi.encoders import jsonable_encoder

from jbi import Operation
from jbi.models import Actions
from tests.fixtures.factories import action_context_factory


def test_default_action_serializes(action_factory):
    action = action_factory(
        module="jbi.actions.default",
        parameters={"jira_project_key": "ABC", "steps": {"new": []}},
    )
    action.caller(action_context_factory(operation=Operation.CREATE))
    serialized_action = jsonable_encoder(action)
    assert not serialized_action.get("_caller")
    assert not serialized_action.get("caller")


@pytest.mark.parametrize("value", [123456, [123456], [12345, 67890], "tbd"])
def test_valid_bugzilla_user_ids(action_factory, value):
    action = action_factory(bugzilla_user_id=value)
    assert action.bugzilla_user_id == value


@pytest.mark.parametrize("value", [None, "foobar@example.com"])
def test_invalid_bugzilla_user_ids(action_factory, value):
    with pytest.raises(pydantic.ValidationError):
        action = action_factory(bugzilla_user_id=value)


def test_no_actions_fails():
    with pytest.raises(ValueError) as exc_info:
        Actions.parse_obj([])
    assert "ensure this value has at least 1 items" in str(exc_info.value)


def test_unknown_module_fails():
    with pytest.raises(ValueError) as exc_info:
        Actions.parse_obj([{"whiteboard_tag": "x", "module": "path.to.unknown"}])
    assert "unknown Python module `path.to.unknown`" in str(exc_info.value)


def test_bad_module_fails():
    with pytest.raises(ValueError) as exc_info:
        # use a module that exists in the source, but isn't properly set up as
        # a valid action module
        Actions.parse_obj([{"whiteboard_tag": "x", "module": "jbi.runner"}])
    assert "action 'jbi.runner' is not properly setup" in str(exc_info.value)


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
