# pylint: disable=cannot-enumerate-pytest-fixtures
import pytest

from src.jbi import configuration as jbi_configuration


def test_mock_jbi_files():
    with pytest.raises(jbi_configuration.ProcessError):
        jbi_configuration.get_yaml_configurations(jbi_config_file="test.yaml")


def test_actual_jbi_files():
    jbi_map = jbi_configuration.get_yaml_configurations()
    assert jbi_map
