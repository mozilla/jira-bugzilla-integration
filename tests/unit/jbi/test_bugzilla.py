"""
Module for testing src/jbi/configuration.py functionality
"""
# pylint: disable=cannot-enumerate-pytest-fixtures
import pytest

from src.jbi import bugzilla


def test_get_jira_labels_without_whiteboard():
    test_bug = bugzilla.BugzillaBug(id=0, whiteboard="")
    jira_labels = test_bug.get_jira_labels()
    assert ["bugzilla"] == jira_labels


def test_get_jira_labels_with_space():
    test_bug = bugzilla.BugzillaBug(id=0, whiteboard="[test whiteboard]")
    jira_labels = test_bug.get_jira_labels()
    expected = ["bugzilla", "test.whiteboard", "[test.whiteboard]"]
    assert expected == jira_labels


def test_get_jira_labels_without_space():
    test_bug = bugzilla.BugzillaBug(id=0, whiteboard="[test-whiteboard]")
    jira_labels = test_bug.get_jira_labels()
    expected = ["bugzilla", "test-whiteboard", "[test-whiteboard]"]
    assert expected == jira_labels


def test_get_jira_labels_multiple():
    test_bug = bugzilla.BugzillaBug(
        id=0, whiteboard="[test whiteboard][test-no-space][test-both space-and-not"
    )
    jira_labels = test_bug.get_jira_labels()
    expected = [
        "bugzilla",
        "test.whiteboard",
        "test-no-space",
        "test-both.space-and-not",
        "[test.whiteboard]",
        "[test-no-space]",
        "[test-both.space-and-not]",
    ]
    assert expected == jira_labels
