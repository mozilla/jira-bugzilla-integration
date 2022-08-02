from unittest.mock import patch

from fastapi.encoders import jsonable_encoder

from jbi.models import Action


def test_model_serializes():
    """Regression test to assert that action with initialzed Bugzilla client serializes"""
    action = Action.parse_obj(
        {
            "whiteboard_tag": "devtest",
            "contact": "person@example.com",
            "description": "test config",
            "module": "tests.fixtures.bugzilla_action",
        }
    )
    action.caller(payload=action)
    serialized_action = jsonable_encoder(action)
    assert not serialized_action.get("_caller")
    assert not serialized_action.get("caller")
