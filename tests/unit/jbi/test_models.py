from unittest.mock import patch

from fastapi.encoders import jsonable_encoder

from src.jbi.models import Action


def test_model_serializes():
    """Regression test to assert that action with initialzed Bugzilla client serializes"""
    action = Action.parse_obj(
        {
            "whiteboard_tag": "devtest",
            "contact": "person@example.com",
            "description": "test config",
            "module": "tests.unit.jbi.bugzilla_action",
        }
    )
    action._initialize_caller()
    serialized_action = jsonable_encoder(action)
    assert not serialized_action.get("_caller")


def test_caller_initialization_defered(webhook_create_example, action_example):
    with patch.object(
        Action, "_initialize_caller", wraps=action_example._initialize_caller
    ) as spy:
        # when the action_example has not been called yet
        # then the caller has not been initialized
        assert not action_example._caller

        # when the action is called
        action_example.call(webhook_create_example)
        action_example.call(webhook_create_example)
        # then the caller is only initialized once
        spy.assert_called_once()
