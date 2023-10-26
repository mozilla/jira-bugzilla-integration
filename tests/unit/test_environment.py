"""Tests for our application environment config parsing

Pytest overwrites your local environment with the values set by options in
`tool.pytest.ini_options`
"""

from jbi.environment import Settings, Environment


def test_settings_env_is_enum_string():
    settings = Settings(env=Environment.PROD)

    assert settings.env == "prod"
    assert str(settings.env) == "prod"
