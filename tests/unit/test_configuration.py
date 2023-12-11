import pytest

from jbi import configuration


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


def test_filename_uses_env(mocker, actions, settings):
    get_actions_from_file_spy = mocker.spy(configuration, "get_actions_from_file")
    assert settings.env == "local"

    configuration.get_actions()

    get_actions_from_file_spy.assert_called_with("config/config.local.yaml")
