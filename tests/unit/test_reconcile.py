"""The reconciliation report (R-13): read-only drift detection."""

from datetime import datetime, timezone
from unittest import mock

import pytest

from jbi import reconcile
from jbi.identity import IdentityEntry, IdentityMap
from jbi.models import Actions
from jbi.reconcile import Kind, check_pair

NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)

SYNC_ALL = {
    "new": ["create_issue", "add_link_to_bugzilla", "add_link_to_jira"],
    "existing": [
        "update_issue_summary",
        "maybe_assign_jira_user",
        "maybe_update_issue_status",
        "maybe_update_issue_priority",
    ],
}


@pytest.fixture
def action(action_factory):
    return action_factory(
        whiteboard_tag="devtest",
        parameters__jira_project_key="JBI",
        parameters__jira_inbound_enabled=True,
        parameters__status_map={"ASSIGNED": "In Progress", "FIXED": "Done"},
        parameters__resolution_map={"FIXED": "Done", "WONTFIX": "Won't Do"},
        parameters__steps=SYNC_ALL,
    )


def fields(
    status="In Progress",
    category="indeterminate",
    resolution=None,
    priority="P2",
    assignee="owner@mozilla.com",
    account="acc-1",
    summary="A bug",
    labels=(),
):
    return {
        "status": {"name": status, "statusCategory": {"key": category}},
        "resolution": {"name": resolution} if resolution else None,
        "priority": {"name": priority} if priority else None,
        "assignee": (
            {"accountId": account, "emailAddress": assignee} if account else None
        ),
        "summary": summary,
        "labels": list(labels),
    }


def bug(bug_factory, **kwargs):
    defaults = dict(
        id=1,
        status="ASSIGNED",
        resolution="",
        priority="P2",
        assigned_to="owner@mozilla.com",
        summary="A bug",
        groups=[],
        is_private=False,
    )
    return bug_factory(**{**defaults, **kwargs})


def kinds(findings):
    return sorted(f.kind for f in findings)


def test_pair_in_sync_has_no_findings(action, bug_factory):
    assert check_pair(action, bug(bug_factory), "JBI-1", fields(), IdentityMap()) == []


def test_status_differs_from_the_mapped_status(action, bug_factory):
    found = check_pair(
        action,
        bug(bug_factory),
        "JBI-1",
        fields(status="To Do", category="new"),
        IdentityMap(),
    )
    assert kinds(found) == [Kind.STATUS]
    assert "In Progress" in found[0].detail


def test_open_vs_closed_is_flagged_without_a_mapping(action, bug_factory):
    found = check_pair(
        action,
        bug(bug_factory, status="NEW"),  # NEW has no status_map entry
        "JBI-1",
        fields(status="Done", category="done", resolution="Done"),
        IdentityMap(),
    )
    assert Kind.STATUS in kinds(found)


def test_resolution_drift_when_both_are_closed(action, bug_factory):
    found = check_pair(
        action,
        bug(bug_factory, status="RESOLVED", resolution="FIXED"),
        "JBI-1",
        fields(status="Done", category="done", resolution="Won't Do"),
        IdentityMap(),
    )
    assert Kind.RESOLUTION in kinds(found)


def test_closed_in_jira_with_nothing_to_map_to(action, bug_factory):
    """Plan 4.1: no mapping and no default -> a person sets the resolution."""
    found = check_pair(
        action,
        bug(bug_factory, status="RESOLVED", resolution="FIXED"),
        "JBI-1",
        fields(status="Done", category="done", resolution="Completed"),
        IdentityMap(),
    )
    assert Kind.CLOSED_WITHOUT_RESOLUTION in kinds(found)


def test_a_default_reverse_resolution_means_nothing_to_report(
    action_factory, bug_factory
):
    action = action_factory(
        parameters__jira_project_key="JBI",
        parameters__jira_inbound_enabled=True,
        parameters__status_map={"FIXED": "Done"},
        parameters__default_reverse_resolution="FIXED",
    )
    found = check_pair(
        action,
        bug(bug_factory, status="RESOLVED", resolution="FIXED"),
        "JBI-1",
        fields(status="Done", category="done", resolution="Completed"),
        IdentityMap(),
    )
    assert Kind.CLOSED_WITHOUT_RESOLUTION not in kinds(found)


def test_fields_the_team_does_not_sync_are_not_drift(action_factory, bug_factory):
    """Found on the first sandbox run: a team without
    `maybe_update_issue_priority` has no Jira priorities, by its own config."""
    action = action_factory(
        parameters__jira_project_key="JBI",
        parameters__jira_inbound_enabled=True,
        parameters__status_map={"ASSIGNED": "In Progress"},
        parameters__steps={"existing": ["update_issue_summary"]},
    )
    found = check_pair(
        action,
        bug(bug_factory, priority="P1", assigned_to="a@mozilla.com"),
        "JBI-1",
        fields(status="To Do", category="indeterminate", priority=None, account=None),
        IdentityMap(),
    )
    assert found == []
    assert reconcile.synced_fields(action) == {"summary"}


def test_priority_drift(action, bug_factory):
    found = check_pair(
        action,
        bug(bug_factory, priority="P1"),
        "JBI-1",
        fields(priority="P3"),
        IdentityMap(),
    )
    assert kinds(found) == [Kind.PRIORITY]


@pytest.mark.parametrize(
    "bmo_assignee,jira,expected",
    [
        ("owner@mozilla.com", dict(assignee="other@mozilla.com"), [Kind.ASSIGNEE]),
        ("owner@mozilla.com", dict(account=None), [Kind.ASSIGNEE]),
        ("nobody@mozilla.org", dict(account=None), []),
        ("nobody@mozilla.org", dict(assignee="x@mozilla.com"), [Kind.ASSIGNEE]),
        ("owner@mozilla.com", dict(assignee=None), [Kind.UNRESOLVED_IDENTITY]),
        ("Owner@Mozilla.com", dict(assignee="owner@mozilla.com"), []),
    ],
)
def test_assignee(action, bug_factory, bmo_assignee, jira, expected):
    found = check_pair(
        action,
        bug(bug_factory, assigned_to=bmo_assignee),
        "JBI-1",
        fields(**jira),
        IdentityMap(),
    )
    assert kinds(found) == expected


def test_identity_map_resolves_a_hidden_email(action, bug_factory):
    identity = IdentityMap(
        users=[IdentityEntry(bmo_email="owner@mozilla.com", jira_account_id="acc-1")]
    )
    found = check_pair(
        action,
        bug(bug_factory),
        "JBI-1",
        fields(assignee=None, account="acc-1"),
        identity,
    )
    assert found == []


def test_summary_drift_never_reveals_the_text(action, bug_factory):
    found = check_pair(
        action,
        bug(bug_factory, summary="public title"),
        "JBI-1",
        fields(summary="embargoed: CVE-2026-0001 in foo"),
        IdentityMap(),
    )
    assert kinds(found) == [Kind.SUMMARY]
    dumped = found[0].model_dump_json()
    assert "CVE" not in dumped and "public title" not in dumped


@pytest.mark.parametrize(
    "jql,expected",
    [
        ("project in (AIACT, aiasst) AND labels = bugzilla", {"AIACT", "AIASST"}),
        ('project = "VPN"', {"VPN"}),
        ("project = FXVPN2 OR project in (AIAUG)", {"FXVPN2", "AIAUG"}),
        ("", set()),
    ],
)
def test_rule_projects(jql, expected):
    assert reconcile.rule_projects(jql) == expected


def test_rule_scope_findings_both_directions():
    found = reconcile.rule_scope_findings("project in (AIACT, OLD)", ["AIACT", "VPN"])
    details = {f.project: f.detail for f in found}
    assert set(details) == {"VPN", "OLD"}
    assert "never reach JBI" in details["VPN"]
    assert "ignored" in details["OLD"]


# --- build_report, with fake clients ---------------------------------------


def _clients(bugs, issues):
    bugzilla = mock.Mock(base_url="https://bmo.test")
    bugzilla._call.return_value = {"bugs": bugs}
    jira = mock.Mock()
    jira.enhanced_jql.return_value = {
        "issues": [{"key": key, "fields": f} for key, f in issues.items()]
    }
    return bugzilla, jira


def _raw(bug_id, **kwargs):
    raw = {
        "id": bug_id,
        "summary": "A bug",
        "status": "ASSIGNED",
        "resolution": "",
        "priority": "P2",
        "assigned_to": "owner@mozilla.com",
        "whiteboard": "[devtest]",
        "see_also": [f"https://mozilla.atlassian.net/browse/JBI-{bug_id}"],
        "groups": [],
        "is_private": False,
    }
    return {**raw, **kwargs}


def test_build_report(action, action_factory):
    not_onboarded = action_factory(
        whiteboard_tag="other", parameters__jira_project_key="OTHER"
    )
    bugzilla, jira = _clients(
        [
            _raw(1),  # in sync
            _raw(2, priority="P1"),  # drift
            _raw(3, see_also=[]),  # never synced
            _raw(4),  # issue gone
            _raw(5, groups=["core-security"]),  # restricted: skipped
            _raw(6),  # sync stopped: skipped
            _raw(7, whiteboard="[devtestx]"),  # substring over-match: dropped
        ],
        {
            "JBI-1": fields(),
            "JBI-2": fields(priority="P3"),
            "JBI-6": fields(priority="P5", labels=["jbi-sync-stop"]),
        },
    )

    report = reconcile.build_report(
        Actions(root=[action, not_onboarded]),
        bugzilla,
        jira,
        IdentityMap(),
        since=None,
        rule_jql="project in (JBI)",
        now=NOW,
    )

    assert report.actions == ["devtest"]
    assert report.pairs_checked == 2
    assert report.skipped == {"restricted": 1, "sync_stopped": 1}
    assert report.counts == {
        "priority_drift": 1,
        "not_synced": 1,
        "issue_missing": 1,
    }
    assert bugzilla._call.call_args.args[0] == "GET"  # read-only
    assert not jira.method_calls[1:]  # one search, nothing else


def test_rule_scope_unset_is_noted(action):
    bugzilla, jira = _clients([], {})
    report = reconcile.build_report(
        Actions(root=[action]), bugzilla, jira, IdentityMap(), None, None, now=NOW
    )
    assert any("JIRA_INBOUND_RULE_JQL" in note for note in report.notes)


def test_jira_lookups_are_batched(action):
    bugs = [_raw(i) for i in range(1, 121)]
    bugzilla, jira = _clients(bugs, {f"JBI-{i}": fields() for i in range(1, 121)})

    report = reconcile.build_report(
        Actions(root=[action]), bugzilla, jira, IdentityMap(), None, None, now=NOW
    )

    assert jira.enhanced_jql.call_count == 3  # 50 + 50 + 20
    assert report.pairs_checked == 120


def test_since_is_sent_to_bugzilla(action):
    from urllib.parse import parse_qs, urlparse

    bugzilla, jira = _clients([], {})
    reconcile.build_report(
        Actions(root=[action]),
        bugzilla,
        jira,
        IdentityMap(),
        since=reconcile.since_days(30, now=NOW),
        rule_jql=None,
        now=NOW,
    )

    query = parse_qs(urlparse(bugzilla._call.call_args.args[1]).query)
    assert query["last_change_time"] == ["2026-10-21T00:00:00Z"]
    assert query["status_whiteboard"] == ["[devtest"]


def test_publish_logs_and_gauges(action, bug_factory, caplog):
    import logging

    report = reconcile.Report(
        generated_at=NOW,
        since=None,
        actions=["devtest"],
        pairs_checked=1,
        findings=check_pair(
            action, bug(bug_factory, priority="P1"), "JBI-1", fields(), IdentityMap()
        ),
    )
    report.counts = {"priority_drift": 1}

    with (
        mock.patch("jbi.reconcile.statsd") as statsd,
        caplog.at_level(logging.WARNING, logger="jbi.reconcile"),
    ):
        reconcile.publish(report)

    statsd.gauge.assert_any_call("jbi.reconcile.findings.priority_drift", 1)
    statsd.gauge.assert_any_call("jbi.reconcile.findings.status_drift", 0)
    assert any("priority_drift" in r.getMessage() for r in caplog.records)


def test_markdown_rendering(action, bug_factory):
    report = reconcile.Report(
        generated_at=NOW,
        since=None,
        actions=["devtest"],
        findings=check_pair(
            action, bug(bug_factory, priority="P1"), "JBI-1", fields(), IdentityMap()
        ),
    )
    text = reconcile.to_markdown(report)
    assert "| priority_drift | 1 | JBI-1 | P1 | P2 |" in text

    empty = reconcile.Report(generated_at=NOW, since=None, actions=["devtest"])
    assert "No drift found." in reconcile.to_markdown(empty)


def test_cli_writes_the_report(action, tmp_path, mocker):
    from click.testing import CliRunner

    from jbi.__main__ import cli

    mocker.patch("jbi.__main__.get_actions", return_value=Actions(root=[action]))
    bugzilla, jira = _clients([_raw(1, priority="P1")], {"JBI-1": fields()})
    mocker.patch(
        "jbi.bugzilla.service.get_service", return_value=mock.Mock(client=bugzilla)
    )
    mocker.patch("jbi.jira.get_service", return_value=mock.Mock(client=jira))
    mocker.patch("jbi.identity.get_identity_map", return_value=IdentityMap())
    mocker.patch("logging.config.dictConfig")
    out = tmp_path / "report.json"

    result = CliRunner().invoke(cli, ["reconcile", "--days", "0", "--output", str(out)])

    assert result.exit_code == 0, result.output
    assert '"priority_drift": 1' in out.read_text()


def test_cli_stdout_is_pure_json(action, mocker):
    """Found on the sandbox run: log lines on stdout broke `| jq`."""
    import json

    from click.testing import CliRunner

    from jbi.__main__ import cli

    mocker.patch("jbi.__main__.get_actions", return_value=Actions(root=[action]))
    bugzilla, jira = _clients([_raw(1, priority="P1")], {"JBI-1": fields()})
    mocker.patch(
        "jbi.bugzilla.service.get_service", return_value=mock.Mock(client=bugzilla)
    )
    mocker.patch("jbi.jira.get_service", return_value=mock.Mock(client=jira))
    mocker.patch("jbi.identity.get_identity_map", return_value=IdentityMap())
    configure = mocker.patch("logging.config.dictConfig")

    result = CliRunner().invoke(cli, ["reconcile", "--days", "0"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["counts"] == {"priority_drift": 1}
    stream = configure.call_args.args[0]["handlers"]["console"]["stream"]
    assert getattr(stream, "name", "") == "<stderr>"
