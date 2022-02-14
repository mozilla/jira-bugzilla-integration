# pylint: disable=cannot-enumerate-pytest-fixtures
import pytest

from src.core import actions as test_actions


def test_valid_actions_context_succeeds():
    for (
        key
    ) in (
        test_actions.module_dict.keys()  # pylint: disable=consider-iterating-dictionary
    ):
        assert test_actions.get_action_context_by_key(key=key)


def test_invalid_actions_context_fails():
    original_dict = test_actions.module_dict.copy()
    test_actions.module_dict["mock"] = None
    with pytest.raises(AssertionError):
        for (
            key
        ) in (
            test_actions.module_dict.keys()  # pylint: disable=consider-iterating-dictionary
        ):
            assert test_actions.get_action_context_by_key(key=key)
    test_actions.module_dict = original_dict  # reset globals
