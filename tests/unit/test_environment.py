"""Tests for our application environment config parsing

Pytest overwrites your local environment with the values set by options in
`tool.pytest.ini_options`
"""

import pydantic
import pytest

from jbi.environment import Environment, Settings


def test_settings_env_is_enum_string():
    settings = Settings(env=Environment.PROD)

    assert settings.env == "prod"
    assert str(settings.env) == "prod"


def test_sentry_dsn():
    Settings(sentry_dsn="http://www.example.com/")


def test_sentry_dsn_no_url_string_raises():
    with pytest.raises(pydantic.ValidationError):
        Settings(sentry_dsn="foobar")


def dl_queue_dsn_allowed_schema(dsn):
    Settings(dl_queue_dsn="file://tmp/queue")


@pytest.mark.parametrize("dsn", ["http://www.example.com", "foobar"])
def invalid_dl_queue_dsn_raises(dsn):
    with pytest.raises(pydantic.ValidationError):
        Settings(dl_queue_dsn=dsn)
