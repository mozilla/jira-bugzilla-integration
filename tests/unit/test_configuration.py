import pytest

from jbi import configuration


def test_mock_jbi_files():
    with pytest.raises(configuration.ConfigError) as exc_info:
        configuration.get_actions(jbi_config_file="tests/fixtures/bad-config.yaml")
    assert "Errors exist" in str(exc_info.value)


def test_actual_jbi_files():
    assert configuration.get_actions(jbi_config_file="config/config.nonprod.yaml")
    assert configuration.get_actions(jbi_config_file="config/config.prod.yaml")
