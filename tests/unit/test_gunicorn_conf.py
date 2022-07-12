import pytest
from pydantic import ValidationError

from bin.gunicorn_conf import GunicornSettings


def test_set_bind(monkeypatch):
    expected = "1.2.3.4:5678"
    monkeypatch.setenv("BIND", expected)
    settings = GunicornSettings()
    assert settings.bind == expected


class TestSetWorkers:
    def test_workers_env_var(self, monkeypatch):
        expected = 3
        monkeypatch.setenv("WORKERS", str(expected))
        settings = GunicornSettings()
        assert settings.workers == expected

    def test_web_concurency_env_var(self, monkeypatch):
        expected = 3
        monkeypatch.setenv("WEB_CONCURRENCY", str(expected))
        settings = GunicornSettings()
        assert settings.workers == expected

    def test_web_concurency_env_var_out_of_range(self, monkeypatch):
        expected = 0
        monkeypatch.setenv("WEB_CONCURRENCY", str(expected))
        with pytest.raises(ValidationError):
            GunicornSettings()

    def test_default_workers(self, monkeypatch):
        expected = 2
        monkeypatch.setattr("multiprocessing.cpu_count", lambda: 2)
        settings = GunicornSettings()
        assert settings.workers == expected

    def test_max_workers(self, monkeypatch):
        expected = 1
        monkeypatch.setattr("multiprocessing.cpu_count", lambda: 2)
        monkeypatch.setenv("MAX_WORKERS", str(expected))
        settings = GunicornSettings()
        assert settings.workers == expected
