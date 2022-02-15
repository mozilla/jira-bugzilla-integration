# pylint: disable=cannot-enumerate-pytest-fixtures
import pytest

from src.core.configurator import actions, per_file_process, process_all_files_in_path
from tests.unit.core import mock_actions


def test_file_processing_empty_file():
    action_key = "test"
    actions.module_dict[action_key] = mock_actions

    default_dict = {"action": "test_action"}
    req_keys = ["enabled"]

    filename = "tests/unit/core/mock_config_files/empty.json"

    key, value = per_file_process(
        filename=filename,
        required_keys=req_keys,
        ret_dict=default_dict,
        action_key=action_key,
        filename_key="filename",
    )
    assert not key
    assert not value


def test_file_processing_disabled_file():
    action_key = "test"
    default_dict = {"action": "test_action"}
    req_keys = ["enabled"]
    filename = "tests/unit/core/mock_config_files/disabled.json"
    actions.module_dict[action_key] = mock_actions
    key, value = per_file_process(
        filename=filename,
        required_keys=req_keys,
        ret_dict=default_dict,
        action_key=action_key,
        filename_key="filename",
    )
    assert not key
    assert not value


def test_file_processing_enabled_file():
    action_key = "test"
    default_dict = {"action": "test_action"}
    req_keys = ["enabled"]
    filename = "tests/unit/core/mock_config_files/enabled.json"
    actions.module_dict[action_key] = mock_actions
    key, value = per_file_process(
        filename=filename,
        required_keys=req_keys,
        ret_dict=default_dict,
        action_key=action_key,
        filename_key="filename",
    )
    assert key, "A value should be extracted."
    assert key in filename
    assert value
    assert "action" in value.keys()


def test_file_processing_enabled_file_unknown_action():
    action_key = "test"
    default_dict = {"action": "test_action"}
    req_keys = ["enabled"]
    filename = "tests/unit/core/mock_config_files/unknown_action.json"
    actions.module_dict[action_key] = mock_actions
    with pytest.raises(AssertionError):
        per_file_process(
            filename=filename,
            required_keys=req_keys,
            ret_dict=default_dict,
            action_key=action_key,
            filename_key="filename",
        )


def test_config_path_throws_exception():
    def raise_except(filename):
        raise Exception

    with pytest.raises(AssertionError):
        process_all_files_in_path(
            folder_path="tests/unit/core/mock_config_files/", process=raise_except
        )


def test_config_path_processing_none():
    def no_key_value(filename):  # pylint: disable=unused-argument
        return None, None

    processed = process_all_files_in_path(
        folder_path="tests/unit/core/mock_config_files/", process=no_key_value
    )
    assert (
        not processed
    ), "Process returns None,None; as such it shouldn't be added to the dictionary"


def test_config_path_processing_success():
    def random_key_value(filename):
        slug = filename.split("/")[-1]
        return slug, {"inner_key": filename}

    processed = process_all_files_in_path(
        folder_path="tests/unit/core/mock_config_files/", process=random_key_value
    )
    assert processed, "The process should return a dictionary"
    for key, value in processed.items():
        assert key in [
            "enabled.json",
            "disabled.json",
            "empty.json",
            "unknown_action.json",
        ]
        assert value
        assert value.get("inner_key")
        assert "tests/unit/core/mock_config_files/" in value.get("inner_key")
