"""Fixtures for the end-to-end smoke tests (plan D12).

Everything here is designed so that a missing credential means "skip", never
"run against the wrong system": the suite refuses to start unless Jira looks
like a sandbox and Bugzilla like a dev instance, the same guard
`.local-harness/run_jbi.sh` applies to the JBI under test.
"""

import os
from urllib.parse import urlparse

import pytest

from .sandbox import Sandbox

REQUIRED_ENV = (
    "JBI_E2E_BASE_URL",
    "JBI_E2E_WHITEBOARD_TAG",
    "JBI_E2E_JIRA_PROJECT",
    "JBI_E2E_BUGZILLA_PRODUCT",
    "JBI_E2E_BUGZILLA_COMPONENT",
    "JIRA_BASE_URL",
    "JIRA_USERNAME",
    "JIRA_API_KEY",
    "BUGZILLA_BASE_URL",
    "BUGZILLA_API_KEY",
    "JBI_API_KEY",
)

SLA_SECONDS = 300  # PRD: changes propagate within 5 minutes.


def _looks_safe(env) -> list[str]:
    problems = []
    jira_host = urlparse(env["JIRA_BASE_URL"]).hostname or ""
    bmo_host = urlparse(env["BUGZILLA_BASE_URL"]).hostname or ""
    if "sandbox" not in jira_host:
        problems.append(f"JIRA_BASE_URL host {jira_host!r} is not a sandbox")
    if not any(word in bmo_host for word in ("dev", "allizom", "localhost")):
        problems.append(f"BUGZILLA_BASE_URL host {bmo_host!r} is not a dev instance")
    return problems


@pytest.fixture(scope="session")
def sandbox():
    """The sandbox, or a skip. Cleans up everything the session created."""
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        pytest.skip("e2e smoke tests need a sandbox; missing: " + ", ".join(missing))
    env = {name: os.environ[name] for name in REQUIRED_ENV}
    if not os.environ.get("JBI_E2E_ALLOW_NON_SANDBOX"):
        problems = _looks_safe(env)
        if problems:
            pytest.exit("REFUSING to run e2e: " + "; ".join(problems), returncode=2)

    client = Sandbox(env)
    yield client
    if os.environ.get("JBI_E2E_KEEP"):
        print(
            f"\nJBI_E2E_KEEP set; left bugs {client.created_bugs} "
            f"and issues {client.created_issues}"
        )
        return
    problems = client.cleanup()
    if problems:
        print("\ne2e cleanup problems: " + "; ".join(problems))


@pytest.fixture
def synced_bug(sandbox):
    """A tagged bug that JBI has turned into a Jira issue."""

    def _make(summary="synced bug", **fields):
        bug_id = sandbox.file_bug(summary, **fields)
        sandbox.post_bugzilla_event(bug_id, "create")
        key = sandbox.track_issue(sandbox.linked_issue(bug_id))
        assert key, f"bug {bug_id} was not linked to a {sandbox.project} issue"
        return bug_id, key

    return _make
