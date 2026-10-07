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


def test_filename_uses_env(mocker, settings):
    get_actions_from_file_spy = mocker.spy(configuration, "get_actions_from_file")
    assert settings.env == "local"

    configuration.get_actions()

    get_actions_from_file_spy.assert_called_with("config/config.local.yaml")


@pytest.mark.parametrize(
    "config_file",
    [
        "config/config.local.yaml",
        "config/config.nonprod.yaml",
        "config/config.prod.yaml",
    ],
)
def test_bidirectional_sync_scaffolding_defaults_in_real_configs(config_file):
    """None of today's real config files set the new Phase 1 scaffolding
    fields (docs/bmo-jira-bidirectional-integration-plan.md, D1), so every
    configured action must resolve them to their no-op defaults. This is the
    "zero behavior change with default config" acceptance criteria for D1."""
    actions = configuration.get_actions_from_file(jbi_config_file=config_file)
    for action in actions:
        params = action.parameters
        assert params.sync_products_components is None
        assert params.min_priority is None
        assert params.min_severity is None
        assert params.identity_map_enabled is False
        assert params.jira_inbound_enabled is False
