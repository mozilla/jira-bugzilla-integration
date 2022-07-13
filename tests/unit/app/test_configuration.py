"""
Module for testing src/app/configuration.py functionality
"""
# pylint: disable=cannot-enumerate-pytest-fixtures
import pytest

from src.app import configuration
from src.jbi.models import Actions


def test_mock_jbi_files():
    with pytest.raises(configuration.ConfigError) as exc_info:
        configuration.get_actions(jbi_config_file="tests/unit/app/bad-config.yaml")
    assert "Errors exist" in str(exc_info.value)


def test_actual_jbi_files():
    jbi_map = configuration.get_actions()
    assert jbi_map


def test_no_actions_fails():
    with pytest.raises(ValueError) as exc_info:
        Actions.parse_obj([])
    assert "no actions configured" in str(exc_info.value)


def test_unknown_module_fails():
    with pytest.raises(ValueError) as exc_info:
        Actions.parse_obj([{"action_tag": "x", "module": "path.to.unknown"}])
    assert "unknown Python module `path.to.unknown`" in str(exc_info.value)


def test_bad_module_fails():
    with pytest.raises(ValueError) as exc_info:
        Actions.parse_obj([{"action_tag": "x", "module": "src.jbi.runner"}])
    assert "action is not properly setup" in str(exc_info.value)
