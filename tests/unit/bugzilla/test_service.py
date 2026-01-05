import pytest

from jbi import bugzilla


@pytest.fixture
def bugzilla_client(settings):
    return bugzilla.client.BugzillaClient(
        base_url=settings.bugzilla_base_url, api_key=settings.bugzilla_api_key
    )


@pytest.fixture
def bugzilla_service(bugzilla_client):
    return bugzilla.service.BugzillaService(bugzilla_client)


def test_refresh_bug_data_keeps_comment_and_attachment(
    bugzilla_service, mocked_responses, bug_factory, settings
):
    bug = bug_factory(with_attachment=True, with_comment=True)
    # https://bugzilla.readthedocs.io/en/latest/api/core/v1/bug.html#get-bug
    mocked_responses.add(
        "GET",
        f"{settings.bugzilla_base_url}/rest/bug/%s" % bug.id,
        json={
            "bugs": [
                {
                    "id": bug.id,
                }
            ],
        },
    )

    updated = bugzilla_service.refresh_bug_data(bug)

    assert updated.comment == bug.comment
    assert updated.attachment == bug.attachment


def test_get_bugs_by_ids_successful_fetch(
    bugzilla_service, mocked_responses, settings
):
    """Test that get_bugs_by_ids successfully fetches multiple bugs."""
    bug_ids = [123, 456, 789]

    # Mock successful responses for all bugs
    for bug_id in bug_ids:
        mocked_responses.add(
            "GET",
            f"{settings.bugzilla_base_url}/rest/bug/{bug_id}",
            json={"bugs": [{"id": bug_id, "summary": f"Bug {bug_id}"}]},
        )

    result = bugzilla_service.get_bugs_by_ids(bug_ids)

    assert len(result) == 3
    assert 123 in result
    assert 456 in result
    assert 789 in result
    assert result[123].id == 123
    assert result[456].id == 456
    assert result[789].id == 789


def test_get_bugs_by_ids_silently_skips_private_bugs(
    bugzilla_service, mocked_responses, settings
):
    """Test that get_bugs_by_ids silently skips private/inaccessible bugs."""
    bug_ids = [123, 456, 789]

    # Mock: bug 123 succeeds, bug 456 returns 401 (inaccessible), bug 789 succeeds
    mocked_responses.add(
        "GET",
        f"{settings.bugzilla_base_url}/rest/bug/123",
        json={"bugs": [{"id": 123, "summary": "Bug 123"}]},
    )
    # Mock logged_in check for bug 456
    mocked_responses.add(
        "GET",
        f"{settings.bugzilla_base_url}/rest/whoami",
        json={"id": 1, "name": "test@example.com"},
    )
    mocked_responses.add(
        "GET",
        f"{settings.bugzilla_base_url}/rest/bug/456",
        json={"error": True, "message": "You are not authorized to access bug #456"},
        status=401,
    )
    mocked_responses.add(
        "GET",
        f"{settings.bugzilla_base_url}/rest/bug/789",
        json={"bugs": [{"id": 789, "summary": "Bug 789"}]},
    )

    result = bugzilla_service.get_bugs_by_ids(bug_ids)

    # Should only return bugs 123 and 789, skipping 456
    assert len(result) == 2
    assert 123 in result
    assert 456 not in result
    assert 789 in result


def test_get_bugs_by_ids_silently_skips_not_found_bugs(
    bugzilla_service, mocked_responses, settings
):
    """Test that get_bugs_by_ids silently skips bugs that don't exist."""
    bug_ids = [123, 456, 789]

    # Mock: bug 123 succeeds, bug 456 returns 404, bug 789 succeeds
    mocked_responses.add(
        "GET",
        f"{settings.bugzilla_base_url}/rest/bug/123",
        json={"bugs": [{"id": 123, "summary": "Bug 123"}]},
    )
    # Mock logged_in check for bug 456 (called after 404)
    mocked_responses.add(
        "GET",
        f"{settings.bugzilla_base_url}/rest/whoami",
        json={"id": 1, "name": "test@example.com"},
    )
    mocked_responses.add(
        "GET",
        f"{settings.bugzilla_base_url}/rest/bug/456",
        json={"error": True, "message": "Bug #456 does not exist"},
        status=404,
    )
    mocked_responses.add(
        "GET",
        f"{settings.bugzilla_base_url}/rest/bug/789",
        json={"bugs": [{"id": 789, "summary": "Bug 789"}]},
    )

    result = bugzilla_service.get_bugs_by_ids(bug_ids)

    # Should only return bugs 123 and 789, skipping 456
    assert len(result) == 2
    assert 123 in result
    assert 456 not in result
    assert 789 in result
