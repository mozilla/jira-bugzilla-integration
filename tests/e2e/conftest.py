"""Fixtures for the end-to-end scenarios (plan D12).

Everything here is designed so that a missing credential means "skip", never
"run against the wrong system".
"""

import os

import pytest

REQUIRED_ENV = (
    "JBI_E2E_JIRA_PROJECT",
    "JBI_E2E_BUGZILLA_PRODUCT",
    "JBI_E2E_BUGZILLA_COMPONENT",
    "JBI_E2E_BASE_URL",
)

SLA_SECONDS = 300  # PRD: changes propagate within 5 minutes.


@pytest.fixture(scope="session", autouse=True)
def require_sandbox_credentials():
    """Skip the whole module unless a sandbox is explicitly configured."""
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        pytest.skip(
            "e2e scenarios need a sandbox; missing: " + ", ".join(missing),
            allow_module_level=True,
        )


@pytest.fixture(scope="session")
def sandbox():
    """The sandbox coordinates the scenarios run against."""
    return {
        "jira_project": os.environ["JBI_E2E_JIRA_PROJECT"],
        "bugzilla_product": os.environ["JBI_E2E_BUGZILLA_PRODUCT"],
        "bugzilla_component": os.environ["JBI_E2E_BUGZILLA_COMPONENT"],
        "base_url": os.environ["JBI_E2E_BASE_URL"],
    }


@pytest.fixture
def not_implemented():
    """Fail a stubbed scenario with a message that says what is missing."""

    def _fail(what: str):
        pytest.fail(
            f"e2e scenario not wired up yet: {what}. "
            "See tests/e2e/README.md -- this needs sandbox access, not design."
        )

    return _fail
