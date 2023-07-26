from unittest import mock

import pytest

from jbi import configuration, environment


def test_mock_jbi_files():
    with pytest.raises(configuration.ConfigError) as exc_info:
        configuration.get_actions_from_file(
            jbi_config_file="tests/fixtures/bad-config.yaml"
        )
    assert "Errors exist" in str(exc_info.value)


def test_actual_jbi_files():
    assert configuration.get_actions_from_file(
        jbi_config_file="config/config.nonprod.yaml"
    )
    assert configuration.get_actions_from_file(
        jbi_config_file="config/config.prod.yaml"
    )


def test_filename_uses_env():
    configuration.get_actions.cache_clear()
    with mock.patch("jbi.configuration.get_actions_from_file") as mocked:
        configuration.get_actions()
    mocked.assert_called_with("config/config.local.yaml")


def test_settings_env_is_enum_string():
    settings = environment.Settings()
    settings.env = environment.Environment.PROD

    assert settings.env == "prod"
    assert str(settings.env) == "prod"
