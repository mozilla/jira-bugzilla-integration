"""
Module for testing src/jbi/configuration.py functionality
"""
# pylint: disable=cannot-enumerate-pytest-fixtures
import pytest

from src.jbi import configuration


def test_mock_jbi_files():
    with pytest.raises(configuration.ProcessError):
        configuration.get_yaml_configurations(
            jbi_config_file="tests/unit/jbi/test-config.yaml"
        )


def test_actual_jbi_files():
    jbi_map = configuration.get_yaml_configurations()
    assert jbi_map
