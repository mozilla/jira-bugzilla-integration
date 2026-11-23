"""Reconciliation report: where BMO and Jira have drifted apart (R-13).

A read-only, scheduled job (`python -m jbi reconcile`), in the same
standalone-runner shape as `jbi.retry`. For every action onboarded to
bidirectional sync it pairs each tagged bug with its linked Jira issue and
reports what a human should look at:

* execution fields that disagree (status, resolution, priority, assignee,
  summary) -- the conflict rule says BMO wins, but the report never writes;
* issues closed in Jira that BMO could not be given a resolution for, the
  case plan section 4.1 deliberately leaves to a person;
* people JBI cannot match between the two systems (plan section 5), which is
  the alert that drives upkeep of the identity override file;
* tagged bugs that never got an issue, or whose issue is gone;
* projects onboarded in config but missing from the Jira webhook's JQL
  filter, or the reverse (plan section 12).

Findings go to the log (one structured WARNING each), to statsd as gauges,
and to a JSON or Markdown report. Free text never appears in a finding: a
summary drift says *that* the titles differ, not what they say.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Iterable, Optional
from urllib.parse import urlencode

from pydantic import BaseModel
from statsd.defaults.env import statsd

from jbi.bugzilla.models import Bug
from jbi.identity import UNASSIGNED_EMAIL, IdentityMap
from jbi.jira_steps import RESOLVED_STATUSES, invert_resolution_map
from jbi.models import Action, Actions
from jbi.visibility import bug_restriction_reason
from jbi.writeback import sync_is_stopped

logger = logging.getLogger(__name__)

# Jira's `key in (...)` gets long fast; stay well inside URL and JQL limits.
JIRA_BATCH = 50
JIRA_FIELDS = "summary,status,resolution,priority,assignee,labels"
BUGZILLA_FIELDS = (
    "id,summary,status,resolution,priority,severity,assigned_to,whiteboard,"
    "see_also,groups,is_private,product,component,last_change_time"
)


class Kind(str, Enum):
    """What kind of drift a finding is."""

    STATUS = "status_drift"
    RESOLUTION = "resolution_drift"
    CLOSED_WITHOUT_RESOLUTION = "closed_without_resolution"
    PRIORITY = "priority_drift"
    ASSIGNEE = "assignee_drift"
    SUMMARY = "summary_drift"
    UNRESOLVED_IDENTITY = "unresolved_identity"
    NOT_SYNCED = "not_synced"
    ISSUE_MISSING = "issue_missing"
    RULE_SCOPE = "rule_scope_drift"


class Finding(BaseModel):
    """One thing a human should look at."""

    kind: Kind
    action: Optional[str] = None
    project: Optional[str] = None
    bug_id: Optional[int] = None
    issue_key: Optional[str] = None
    bmo: Optional[str] = None
    jira: Optional[str] = None
    detail: str = ""


class Report(BaseModel):
    """The whole run, as written to JSON."""

    generated_at: datetime
    since: Optional[datetime]
    actions: list[str]
    pairs_checked: int = 0
    skipped: dict[str, int] = {}
    counts: dict[str, int] = {}
    findings: list[Finding] = []
    notes: list[str] = []


# --- Pure checks -----------------------------------------------------------


def carries_tag(bug: Bug, tag: str) -> bool:
    """Same whiteboard match the runner uses to pick an action."""
    pattern = r"\[" + re.escape(tag) + r"(-[^\]]*)*\]"
    return bool(re.search(pattern, bug.whiteboard or "", re.IGNORECASE))


# A field is only compared when the action's forward steps sync it. A team
# that does not push priority to Jira, say, has Jira priorities that differ
# from BMO by its own configuration -- that is not drift.
FIELD_STEPS = {
    "status": "maybe_update_issue_status",
    "priority": "maybe_update_issue_priority",
    "assignee": "maybe_assign_jira_user",
    "summary": "update_issue_summary",
}


def synced_fields(action: Action) -> set[str]:
    """The execution fields this action keeps in sync from BMO to Jira."""
    steps = set(action.parameters.steps.new) | set(action.parameters.steps.existing)
    return {field for field, step in FIELD_STEPS.items() if step in steps}


def _name(value: Any) -> Optional[str]:
    return value.get("name") if isinstance(value, dict) else None


def check_pair(
    action: Action,
    bug: Bug,
    issue_key: str,
    fields: dict,
    identity_map: IdentityMap,
) -> list[Finding]:
    """Compare one bug with its linked issue. Pure: no I/O."""
    params = action.parameters
    base = {
        "action": action.whiteboard_tag,
        "project": params.jira_project_key,
        "bug_id": bug.id,
        "issue_key": issue_key,
    }
    findings: list[Finding] = []
    synced = synced_fields(action)

    status = fields.get("status") or {}
    jira_status = status.get("name")
    category = (status.get("statusCategory") or {}).get("key")
    bmo_closed = (bug.status or "") in RESOLVED_STATUSES

    # Status: the exact mapped status when the team's status_map has one, and
    # otherwise at least open-vs-done, which every workflow agrees on.
    expected = params.status_map.get(bug.resolution or bug.status or "")
    if "status" not in synced:
        pass
    elif expected and jira_status and expected != jira_status:
        findings.append(
            Finding(
                kind=Kind.STATUS,
                bmo=f"{bug.status} {bug.resolution or ''}".strip(),
                jira=jira_status,
                detail=f"status_map expects {expected!r}",
                **base,
            )
        )
    elif category and bmo_closed != (category == "done"):
        findings.append(
            Finding(
                kind=Kind.STATUS,
                bmo=f"{bug.status} {bug.resolution or ''}".strip(),
                jira=f"{jira_status} ({category})",
                detail="one side is closed and the other is not",
                **base,
            )
        )

    # Resolution: only meaningful once both sides are closed.
    jira_resolution = _name(fields.get("resolution"))
    if category == "done":
        mapped = (
            invert_resolution_map(params.resolution_map).get(jira_resolution)
            if jira_resolution
            else None
        )
        if bmo_closed and mapped and mapped != bug.resolution:
            findings.append(
                Finding(
                    kind=Kind.RESOLUTION,
                    bmo=bug.resolution,
                    jira=jira_resolution,
                    detail=f"resolution_map maps {jira_resolution!r} to {mapped!r}",
                    **base,
                )
            )
        if not mapped and not params.default_reverse_resolution:
            findings.append(
                Finding(
                    kind=Kind.CLOSED_WITHOUT_RESOLUTION,
                    bmo=bug.resolution or None,
                    jira=jira_resolution,
                    detail="closed in Jira with no BMO resolution to map to; "
                    "a person should set it",
                    **base,
                )
            )

    # Priority, through the same map the forward sync uses.
    expected_priority = params.priority_map.get(bug.priority or "")
    jira_priority = _name(fields.get("priority"))
    if (
        "priority" in synced
        and expected_priority
        and jira_priority
        and expected_priority != jira_priority
    ):
        findings.append(
            Finding(
                kind=Kind.PRIORITY,
                bmo=bug.priority,
                jira=jira_priority,
                detail=f"priority_map expects {expected_priority!r}",
                **base,
            )
        )

    if "assignee" in synced:
        findings.extend(
            _assignee_findings(bug, fields.get("assignee"), identity_map, base)
        )

    # Summary: say *that* it differs, never what it says (it may be embargoed).
    jira_summary = (fields.get("summary") or "").strip()
    bmo_summary = (bug.summary or "").strip()
    if (
        "summary" in synced
        and jira_summary
        and bmo_summary
        and jira_summary != bmo_summary
    ):
        findings.append(
            Finding(
                kind=Kind.SUMMARY,
                bmo=f"{len(bmo_summary)} chars",
                jira=f"{len(jira_summary)} chars",
                detail="titles differ",
                **base,
            )
        )
    return findings


def _assignee_findings(
    bug: Bug, assignee: Optional[dict], identity_map: IdentityMap, base: dict
) -> list[Finding]:
    bmo_email = (bug.assigned_to or "").strip().lower()
    bmo_assigned = bool(bmo_email) and bmo_email != UNASSIGNED_EMAIL
    account_id = (assignee or {}).get("accountId")

    if not account_id:
        if bmo_assigned:
            mapped = identity_map.jira_account_id_for(bmo_email)
            return [
                Finding(
                    kind=Kind.ASSIGNEE,
                    bmo=bmo_email,
                    jira=None,
                    detail="assigned in BMO, unassigned in Jira"
                    + ("" if mapped else " (no identity-map entry either)"),
                    **base,
                )
            ]
        return []

    jira_email = identity_map.bmo_email_for(account_id) or (
        (assignee or {}).get("emailAddress") or ""
    )
    if not jira_email:
        # Hidden email and no override: JBI cannot tell who this is, in
        # either direction. This is what the identity file is for.
        return [
            Finding(
                kind=Kind.UNRESOLVED_IDENTITY,
                bmo=bmo_email or None,
                jira=account_id,
                detail="Jira assignee has no visible email and no identity-map "
                "entry; add one to config/identity_map.<env>.yaml",
                **base,
            )
        ]
    if jira_email.strip().lower() != bmo_email:
        return [
            Finding(
                kind=Kind.ASSIGNEE,
                bmo=bmo_email or None,
                jira=jira_email.lower(),
                detail="different people assigned",
                **base,
            )
        ]
    return []


_PROJECT_CLAUSE = re.compile(
    r"project\s*(?:in\s*\(([^)]*)\)|=\s*\"?([A-Za-z][A-Za-z0-9_]*)\"?)",
    re.IGNORECASE,
)


def jql_projects(jql: str) -> set[str]:
    """Project keys named in a JQL scope such as `project in (A, B)`."""
    keys: set[str] = set()
    for listed, single in _PROJECT_CLAUSE.findall(jql or ""):
        for key in listed.split(",") if listed else [single]:
            key = key.strip().strip("\"'")
            if key:
                keys.add(key.upper())
    return keys


def rule_scope_findings(jql: Optional[str], onboarded: Iterable[str]) -> list[Finding]:
    """Compare the Jira webhook's JQL filter with the onboarded projects."""
    onboarded_keys = {key.upper() for key in onboarded}
    in_rule = jql_projects(jql or "")
    findings = [
        Finding(
            kind=Kind.RULE_SCOPE,
            project=key,
            detail="onboarded in config but not in the Jira webhook's JQL: "
            "Jira changes in this project never reach JBI",
        )
        for key in sorted(onboarded_keys - in_rule)
    ]
    findings += [
        Finding(
            kind=Kind.RULE_SCOPE,
            project=key,
            detail="in the Jira webhook's JQL but not onboarded in config: "
            "its events are published and then ignored",
        )
        for key in sorted(in_rule - onboarded_keys)
    ]
    return findings


# --- I/O ---------------------------------------------------------------------


def find_tagged_bugs(bugzilla_client, tag: str, since: Optional[datetime]) -> list[Bug]:
    """The bugs carrying `[tag]`, changed since `since` when given."""
    params = {
        "status_whiteboard": f"[{tag}",
        "status_whiteboard_type": "substring",
        "include_fields": BUGZILLA_FIELDS,
    }
    if since is not None:
        params["last_change_time"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    url = f"{bugzilla_client.base_url}/rest/bug?{urlencode(params)}"
    bugs = [
        Bug.model_validate(raw)
        for raw in bugzilla_client._call("GET", url).get("bugs") or []
    ]
    return [bug for bug in bugs if carries_tag(bug, tag)]


def fetch_issues(jira_client, keys: list[str]) -> dict[str, dict]:
    """Issue fields by key, in batches. Keys Jira does not return are absent."""
    issues: dict[str, dict] = {}
    for start in range(0, len(keys), JIRA_BATCH):
        batch = keys[start : start + JIRA_BATCH]
        quoted = ", ".join(f'"{key}"' for key in batch)
        result = (
            jira_client.enhanced_jql(
                f"key in ({quoted})", fields=JIRA_FIELDS, limit=len(batch)
            )
            or {}
        )
        for issue in result.get("issues") or []:
            issues[issue["key"]] = issue.get("fields") or {}
    return issues


def build_report(
    actions: Actions,
    bugzilla_client,
    jira_client,
    identity_map: IdentityMap,
    since: Optional[datetime],
    inbound_jql: Optional[str],
    only_tag: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Report:
    """Run every check for the onboarded actions. Never writes anywhere."""
    onboarded = [
        action
        for action in actions
        if action.enabled
        and action.parameters.jira_inbound_enabled
        and (only_tag is None or action.whiteboard_tag.lower() == only_tag.lower())
    ]
    report = Report(
        generated_at=now or datetime.now(timezone.utc),
        since=since,
        actions=[action.whiteboard_tag for action in onboarded],
    )
    skipped: Counter[str] = Counter()

    for action in onboarded:
        project = action.parameters.jira_project_key
        bugs = find_tagged_bugs(bugzilla_client, action.whiteboard_tag, since)

        linked: dict[str, Bug] = {}
        for bug in bugs:
            if bug_restriction_reason(bug):
                skipped["restricted"] += 1  # never synced, by design
                continue
            key = bug.extract_from_see_also(project_key=project)
            if not isinstance(key, str):
                report.findings.append(
                    Finding(
                        kind=Kind.NOT_SYNCED,
                        action=action.whiteboard_tag,
                        project=project,
                        bug_id=bug.id,
                        detail="tagged bug has no linked Jira issue",
                    )
                )
                continue
            linked[key] = bug

        issues = fetch_issues(jira_client, sorted(linked))
        for key, bug in sorted(linked.items(), key=lambda item: item[1].id):
            fields = issues.get(key)
            if fields is None:
                report.findings.append(
                    Finding(
                        kind=Kind.ISSUE_MISSING,
                        action=action.whiteboard_tag,
                        project=project,
                        bug_id=bug.id,
                        issue_key=key,
                        detail="linked issue not found (deleted, moved, or not "
                        "visible to JBI)",
                    )
                )
                continue
            if sync_is_stopped(
                fields.get("labels") or [], action.parameters.sync_stop_label
            ):
                skipped["sync_stopped"] += 1
                continue
            report.pairs_checked += 1
            report.findings.extend(check_pair(action, bug, key, fields, identity_map))

    if inbound_jql:
        if only_tag is None:
            report.findings.extend(
                rule_scope_findings(
                    inbound_jql, {a.parameters.jira_project_key for a in onboarded}
                )
            )
    else:
        report.notes.append("Jira webhook scope not checked: set JIRA_INBOUND_JQL.")

    report.skipped = dict(skipped)
    report.counts = dict(Counter(f.kind.value for f in report.findings))
    return report


def publish(report: Report) -> None:
    """Log every finding and send the counts to statsd."""
    for finding in report.findings:
        logger.warning(
            "Reconcile: %s on %s / Bug %s",
            finding.kind.value,
            finding.issue_key or finding.project,
            finding.bug_id,
            extra={"finding": finding.model_dump(mode="json")},
        )
    statsd.gauge("jbi.reconcile.pairs_checked", report.pairs_checked)
    for kind in Kind:
        statsd.gauge(
            f"jbi.reconcile.findings.{kind.value}", report.counts.get(kind.value, 0)
        )


def to_markdown(report: Report) -> str:
    """A human-readable rendering of the report."""
    lines = [
        f"# JBI reconciliation report, {report.generated_at:%Y-%m-%d %H:%M} UTC",
        "",
        f"Actions: {', '.join(report.actions) or 'none onboarded'} · "
        f"pairs checked: {report.pairs_checked}"
        + (f" · changed since {report.since:%Y-%m-%d}" if report.since else ""),
        "",
    ]
    if report.skipped:
        lines.append(
            "Skipped: "
            + ", ".join(f"{k} {v}" for k, v in sorted(report.skipped.items()))
        )
        lines.append("")
    for note in report.notes:
        lines.append(f"> {note}")
    if not report.findings:
        lines += ["", "No drift found."]
        return "\n".join(lines) + "\n"
    lines += [
        "",
        "| Kind | Bug | Issue | BMO | Jira | Detail |",
        "|---|---|---|---|---|---|",
    ]
    for f in sorted(
        report.findings, key=lambda f: (f.kind.value, f.project or "", f.bug_id or 0)
    ):
        lines.append(
            f"| {f.kind.value} | {f.bug_id or ''} | {f.issue_key or f.project or ''} | "
            f"{f.bmo or ''} | {f.jira or ''} | {f.detail} |"
        )
    return "\n".join(lines) + "\n"


def to_json(report: Report) -> str:
    return json.dumps(report.model_dump(mode="json"), indent=2)


def since_days(
    days: Optional[int], now: Optional[datetime] = None
) -> Optional[datetime]:
    if days is None:
        return None
    return (now or datetime.now(timezone.utc)) - timedelta(days=days)
