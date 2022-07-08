"""
Module for testing src/app/configuration.py functionality
"""
# pylint: disable=cannot-enumerate-pytest-fixtures
import pytest

from src.app import configuration


def test_mock_jbi_files():
    with pytest.raises(configuration.ConfigError) as exc_info:
        configuration.get_actions(jbi_config_file="tests/unit/app/bad-config.yaml")
    assert str(exc_info.value) == "Errors exist."


def test_actual_jbi_files():
    jbi_map = configuration.get_actions()
    assert jbi_map
