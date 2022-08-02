"""
Module for testing jbi/configuration.py functionality
"""
# pylint: disable=cannot-enumerate-pytest-fixtures
import pytest

from jbi import configuration
from jbi.models import Actions


def test_mock_jbi_files():
    with pytest.raises(configuration.ConfigError) as exc_info:
        configuration.get_actions(jbi_config_file="tests/fixtures/bad-config.yaml")
    assert "Errors exist" in str(exc_info.value)


def test_actual_jbi_files():
    assert configuration.get_actions(jbi_config_file="config/config.nonprod.yaml")
    assert configuration.get_actions(jbi_config_file="config/config.prod.yaml")


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
    action = {
        "whiteboard_tag": "x",
        "contact": "tbd",
        "description": "foo",
        "module": "tests.fixtures.noop_action",
    }
    with pytest.raises(ValueError) as exc_info:
        Actions.parse_obj(
            [
                action,
                {**action, "whiteboard_tag": "y"},
                action,
            ]
        )
    assert "actions have duplicated lookup tags: ['x']" in str(exc_info.value)
