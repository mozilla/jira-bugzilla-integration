import pytest
from fastapi.testclient import TestClient

from src.app.api import app
from src.app.environment import Settings


@pytest.fixture
def anon_client():
    """A test client with no authorization."""
    return TestClient(app)


@pytest.fixture
def settings():
    return Settings()
