import pytest
from fastapi.encoders import jsonable_encoder

from jbi.models import Action, Actions
from tests.fixtures.factories import action_factory


def test_model_serializes():
    """Regression test to assert that action with initialized Bugzilla client serializes"""
    action = Action.parse_obj(
        {
            "whiteboard_tag": "devtest",
            "contact": "person@example.com",
            "description": "test config",
            "module": "tests.fixtures.bugzilla_action",
        }
    )
    action.caller(bug=None, event=None)
    serialized_action = jsonable_encoder(action)
    assert not serialized_action.get("_caller")
    assert not serialized_action.get("caller")


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
        Actions.parse_obj([{"whiteboard_tag": "x", "module": "jbi.runner"}])
    assert "action is not properly setup" in str(exc_info.value)


def test_duplicated_whiteboard_tag_fails():
    with pytest.raises(ValueError) as exc_info:
        Actions.parse_obj(
            [
                action_factory(whiteboard_tag="x"),
                action_factory(whiteboard_tag="y"),
                action_factory(whiteboard_tag="x"),
            ]
        )
    assert "actions have duplicated lookup tags: ['x']" in str(exc_info.value)
