"""Tests for identity resolution (R-11, plan D8)."""

from unittest import mock

import pytest

from jbi import steps
from jbi.identity import (
    UNASSIGNED_EMAIL,
    IdentityEntry,
    IdentityMap,
    get_identity_map_from_file,
)
from jbi.jira import JiraService

MAPPED = IdentityMap(
    users=[
        IdentityEntry(
            bmo_email="mismatch@mozilla.com",
            jira_account_id="account-id-mismatch",
            display_name="Miss Match",
        ),
        IdentityEntry(
            bmo_email="hidden@mozilla.com",
            jira_account_id="account-id-hidden",
        ),
    ]
)


def test_map_resolves_a_mismatched_email():
    assert MAPPED.jira_account_id_for("mismatch@mozilla.com") == "account-id-mismatch"


def test_map_lookup_is_case_insensitive():
    """BMO emails are displayed inconsistently; a case difference must not
    silently drop someone out of the map."""
    assert MAPPED.jira_account_id_for("MisMatch@Mozilla.com") == "account-id-mismatch"


def test_map_returns_none_for_unmapped_email():
    """The common case: not in the map, so resolution falls through to tier 2."""
    assert MAPPED.jira_account_id_for("newhire@mozilla.com") is None


def test_map_returns_none_for_missing_email():
    assert MAPPED.jira_account_id_for(None) is None


def test_map_resolves_reverse_direction():
    assert MAPPED.bmo_email_for("account-id-hidden") == "hidden@mozilla.com"
    assert MAPPED.bmo_email_for("account-id-unknown") is None


def test_map_display_name():
    assert MAPPED.display_name_for("account-id-mismatch") == "Miss Match"
    # An entry without a display name resolves to None rather than inventing one.
    assert MAPPED.display_name_for("account-id-hidden") is None


def test_missing_map_file_is_an_empty_map(tmp_path):
    """Having no overrides is the expected state, not an error."""
    identity_map = get_identity_map_from_file(str(tmp_path / "nope.yaml"))

    assert identity_map.users == []


def test_map_file_is_parsed(tmp_path):
    path = tmp_path / "identity_map.test.yaml"
    path.write_text(
        "users:\n"
        "  - bmo_email: person@mozilla.com\n"
        "    jira_account_id: account-id-person\n"
        "    display_name: A Person\n"
    )

    identity_map = get_identity_map_from_file(str(path))

    assert identity_map.jira_account_id_for("person@mozilla.com") == "account-id-person"


def test_shipped_map_files_are_empty_and_valid():
    """The repo ships the map files with no entries: enabling the feature must
    not silently pick up somebody's leftover override."""
    for env in ("local", "nonprod", "prod"):
        identity_map = get_identity_map_from_file(f"config/identity_map.{env}.yaml")
        assert identity_map.users == []


# --- Tier ordering in the forward assignee step -----------------------------


@pytest.fixture
def assigned_context(action_context_factory):
    from jbi import Operation

    return action_context_factory(
        operation=Operation.CREATE,
        bug__assigned_to="mismatch@mozilla.com",
        jira__issue="JBI-234",
        current_step="maybe_assign_jira_user",
    )


def test_tier1_map_hit_skips_the_email_lookup(
    assigned_context, mocked_jira, action_params_factory
):
    """The point of the map: resolve people whose Jira email is hidden or
    different, which an email lookup cannot do."""
    with mock.patch("jbi.steps.get_identity_map", return_value=MAPPED):
        result, _ = steps.maybe_assign_jira_user(
            context=assigned_context,
            parameters=action_params_factory(identity_map_enabled=True),
            jira_service=JiraService(mocked_jira),
        )

    assert result == steps.StepStatus.SUCCESS
    assert not mocked_jira.user_find_by_user_string.called
    mocked_jira.update_issue_field.assert_called_once_with(
        key="JBI-234", fields={"assignee": {"accountId": "account-id-mismatch"}}
    )


def test_tier2_email_lookup_is_used_when_unmapped(
    action_context_factory, mocked_jira, action_params_factory
):
    context = action_context_factory(
        operation=__import__("jbi").Operation.CREATE,
        bug__assigned_to="newhire@mozilla.com",
        jira__issue="JBI-234",
        current_step="maybe_assign_jira_user",
    )
    mocked_jira.user_find_by_user_string.return_value = [
        {"accountId": "account-id-newhire"}
    ]

    with mock.patch("jbi.steps.get_identity_map", return_value=MAPPED):
        result, _ = steps.maybe_assign_jira_user(
            context=context,
            parameters=action_params_factory(identity_map_enabled=True),
            jira_service=JiraService(mocked_jira),
        )

    assert result == steps.StepStatus.SUCCESS
    assert mocked_jira.user_find_by_user_string.called
    mocked_jira.update_issue_field.assert_called_once_with(
        key="JBI-234", fields={"assignee": {"accountId": "account-id-newhire"}}
    )


def test_map_is_not_consulted_when_disabled(
    assigned_context, mocked_jira, action_params_factory
):
    """Default-OFF: an action that has not opted in behaves exactly as today."""
    mocked_jira.user_find_by_user_string.return_value = [{"accountId": "by-email"}]

    with mock.patch("jbi.steps.get_identity_map", return_value=MAPPED):
        steps.maybe_assign_jira_user(
            context=assigned_context,
            parameters=action_params_factory(identity_map_enabled=False),
            jira_service=JiraService(mocked_jira),
        )

    assert mocked_jira.user_find_by_user_string.called
    mocked_jira.update_issue_field.assert_called_once_with(
        key="JBI-234", fields={"assignee": {"accountId": "by-email"}}
    )


def test_tier3_fallback_when_nobody_resolves(
    action_context_factory, mocked_jira, action_params_factory, capturelogs
):
    """Neither guess an identity nor fail loudly: leave the assignee unset for
    a human to correct."""
    import logging

    from jbi import Operation

    context = action_context_factory(
        operation=Operation.CREATE,
        bug__assigned_to="ghost@mozilla.com",
        jira__issue="JBI-234",
        current_step="maybe_assign_jira_user",
    )
    mocked_jira.user_find_by_user_string.return_value = []

    with mock.patch("jbi.steps.get_identity_map", return_value=MAPPED):
        with capturelogs.for_logger("jbi.steps").at_level(logging.INFO):
            result, _ = steps.maybe_assign_jira_user(
                context=context,
                parameters=action_params_factory(identity_map_enabled=True),
                jira_service=JiraService(mocked_jira),
            )

    assert result == steps.StepStatus.INCOMPLETE
    assert not mocked_jira.update_issue_field.called


def test_unassigned_sentinel_is_never_looked_up(
    action_context_factory, mocked_jira, action_params_factory
):
    """`nobody@mozilla.org` means "no one", not a person to resolve."""
    from jbi import Operation

    context = action_context_factory(
        operation=Operation.CREATE,
        bug__assigned_to=UNASSIGNED_EMAIL,
        jira__issue="JBI-234",
        current_step="maybe_assign_jira_user",
    )

    with mock.patch("jbi.steps.get_identity_map", return_value=MAPPED):
        result, _ = steps.maybe_assign_jira_user(
            context=context,
            parameters=action_params_factory(identity_map_enabled=True),
            jira_service=JiraService(mocked_jira),
        )

    assert result == steps.StepStatus.NOOP
    assert not mocked_jira.user_find_by_user_string.called
    assert not mocked_jira.update_issue_field.called


# --- Seed / drift-check script ---------------------------------------------


@pytest.fixture
def seed_script(mocked_jira):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "seed_identity_map", "bin/seed_identity_map.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_seed_skips_emails_that_resolve_automatically(seed_script, mocked_jira, capsys):
    """An email Jira already resolves does not belong in the map: an entry
    would be dead weight someone has to maintain."""
    mocked_jira.user_find_by_user_string.return_value = [{"accountId": "abc"}]

    with mock.patch.object(seed_script, "get_identity_map", return_value=IdentityMap()):
        seed_script.main(["newhire@mozilla.com"])

    captured = capsys.readouterr()
    assert "resolves automatically" in captured.err
    assert "users:" not in captured.out


def test_seed_emits_a_todo_entry_for_unknown_emails(seed_script, mocked_jira, capsys):
    mocked_jira.user_find_by_user_string.return_value = []

    with mock.patch.object(seed_script, "get_identity_map", return_value=IdentityMap()):
        seed_script.main(["ghost@mozilla.com"])

    captured = capsys.readouterr()
    assert "NOT FOUND" in captured.err
    assert "bmo_email: ghost@mozilla.com" in captured.out


def test_check_reports_stale_account_ids(seed_script, mocked_jira, capsys):
    """Drift detection: the accountId in the map no longer exists in Jira."""
    mocked_jira.user_find_by_user_string.return_value = [
        {"accountId": "a-different-account"}
    ]

    with mock.patch.object(seed_script, "get_identity_map", return_value=MAPPED):
        exit_code = seed_script.main(["--check"])

    assert exit_code == 1
    assert "need attention" in capsys.readouterr().out
