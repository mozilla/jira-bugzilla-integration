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


def test_get_bugs_by_ids_successful_fetch(bugzilla_service, mocked_responses, settings):
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


# --- Reverse (Jira -> BMO) writers, plan D7 --------------------------------


@pytest.fixture
def mocked_service(mocked_bugzilla):
    """A service over a mocked client, for asserting on calls rather than HTTP."""
    return bugzilla.service.BugzillaService(mocked_bugzilla)


def test_set_status_resolution_sends_both_fields_in_one_request(
    mocked_service, mocked_bugzilla, bug_factory
):
    """BMO validates status and resolution together, so they must not be
    written as two separate updates."""
    bug = bug_factory(status="NEW", resolution="")

    mocked_service.set_status_resolution(bug, "RESOLVED", "FIXED")

    mocked_bugzilla.update_bug.assert_called_once_with(
        bug.id, status="RESOLVED", resolution="FIXED"
    )


def test_set_status_resolution_skips_unchanged_values(
    mocked_service, mocked_bugzilla, bug_factory
):
    """The loop-safety unit proof: an echoed value issues no request at all."""
    bug = bug_factory(status="RESOLVED", resolution="FIXED")

    result = mocked_service.set_status_resolution(bug, "RESOLVED", "FIXED")

    assert result is None
    assert not mocked_bugzilla.update_bug.called


def test_set_status_resolution_writes_only_the_changed_field(
    mocked_service, mocked_bugzilla, bug_factory
):
    bug = bug_factory(status="ASSIGNED", resolution="")

    mocked_service.set_status_resolution(bug, "RESOLVED", None)

    mocked_bugzilla.update_bug.assert_called_once_with(bug.id, status="RESOLVED")


@pytest.mark.parametrize(
    "method,field,current,new",
    [
        ("set_assignee", "assigned_to", "nobody@mozilla.org", "person@mozilla.com"),
        ("set_priority", "priority", "P3", "P1"),
        ("set_summary", "summary", "Old title", "New title"),
    ],
)
def test_field_writers_write_when_changed(
    mocked_service, mocked_bugzilla, bug_factory, method, field, current, new
):
    bug = bug_factory(**{field: current})

    getattr(mocked_service, method)(bug, new)

    mocked_bugzilla.update_bug.assert_called_once_with(bug.id, **{field: new})


@pytest.mark.parametrize(
    "method,field,value",
    [
        ("set_assignee", "assigned_to", "person@mozilla.com"),
        ("set_priority", "priority", "P1"),
        ("set_summary", "summary", "Same title"),
    ],
)
def test_field_writers_skip_when_unchanged(
    mocked_service, mocked_bugzilla, bug_factory, method, field, value
):
    bug = bug_factory(**{field: value})

    result = getattr(mocked_service, method)(bug, value)

    assert result is None
    assert not mocked_bugzilla.update_bug.called


def test_field_writers_skip_none(mocked_service, mocked_bugzilla, bug_factory):
    """`None` means "the reverse step had nothing to say about this field",
    which must not be confused with "clear it"."""
    bug = bug_factory(priority="P1")

    assert mocked_service.set_priority(bug, None) is None
    assert not mocked_bugzilla.update_bug.called


def test_add_comment_posts_new_comment(
    mocked_service, mocked_bugzilla, bug_factory, comment_factory
):
    bug = bug_factory()
    mocked_bugzilla.get_comments.return_value = [comment_factory(text="something else")]

    mocked_service.add_comment(bug, "from Jira, by Jane: looks good")

    mocked_bugzilla.update_bug.assert_called_once_with(
        bug.id, comment={"body": "from Jira, by Jane: looks good"}
    )


def test_add_comment_does_not_duplicate_an_existing_comment(
    mocked_service, mocked_bugzilla, bug_factory, comment_factory
):
    """A comment has no field to compare, so the read-before-write equivalent
    is a duplicate check: a replayed event must not append the text twice."""
    bug = bug_factory()
    text = "from Jira, by Jane: looks good"
    mocked_bugzilla.get_comments.return_value = [comment_factory(text=text)]

    result = mocked_service.add_comment(bug, text)

    assert result is None
    assert not mocked_bugzilla.update_bug.called


def test_add_comment_ignores_empty_text(mocked_service, mocked_bugzilla, bug_factory):
    assert mocked_service.add_comment(bug_factory(), "") is None
    assert not mocked_bugzilla.update_bug.called
