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
