# pylint: disable=cannot-enumerate-pytest-fixtures
import pytest

from src.core import actions as test_actions


def test_actions_context():
    for (
        key
    ) in (
        test_actions.module_dict.keys()  # pylint: disable=consider-iterating-dictionary
    ):
        assert test_actions.get_action_context_by_key(key=key)


def test_actions_context_fails():
    test_actions.module_dict["mock"] = None
    with pytest.raises(AssertionError):
        for (
            key
        ) in (
            test_actions.module_dict.keys()  # pylint: disable=consider-iterating-dictionary
        ):
            assert test_actions.get_action_context_by_key(key=key)
