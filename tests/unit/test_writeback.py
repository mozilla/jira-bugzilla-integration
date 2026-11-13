"""Tests for the central write-back policy (plan section 4, D11)."""

import pytest

from jbi.writeback import (
    bmo_wins_conflict,
    is_writeback_allowed,
    suppressed_fields,
)


@pytest.mark.parametrize(
    "field", ["status", "resolution", "priority", "assignee", "summary", "comment"]
)
def test_execution_fields_are_writable(field):
    assert is_writeback_allowed(field) is True


@pytest.mark.parametrize(
    "field",
    ["Sprint", "Story Points", "Epic Link", "parent", "Rank", "labels", "components"],
)
def test_planning_fields_are_never_writable(field):
    """These describe how work is planned, not what it is. Writing them back
    would either fail or pollute a public bug with internal context."""
    assert is_writeback_allowed(field) is False


def test_unknown_fields_are_not_writable():
    """The policy is an allowlist first: a field nobody has thought about
    does not get written to BMO by accident."""
    assert is_writeback_allowed("customfield_10042") is False
    assert is_writeback_allowed(None) is False


def test_field_matching_ignores_case_and_padding():
    assert is_writeback_allowed(" Status ") is True
    assert is_writeback_allowed("SPRINT") is False


def test_suppressed_fields_reports_only_denied_ones():
    assert suppressed_fields(["status", "Sprint", "Epic Link"]) == [
        "sprint",
        "epic link",
    ]


def test_conflict_detected_when_bmo_moved_independently():
    assert bmo_wins_conflict("Old title", "Edited in BMO") is True


def test_no_conflict_when_the_two_systems_agree():
    assert bmo_wins_conflict("Same title", "Same title") is False


def test_no_conflict_when_previous_value_is_unknown():
    """Jira does not always send changelog detail; syncing must continue
    rather than stop the moment it cannot prove there is no conflict."""
    assert bmo_wins_conflict(None, "anything") is False
