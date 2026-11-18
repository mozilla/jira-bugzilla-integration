#!/usr/bin/env python
"""Backfill mirror Epics for a team's existing metabugs (R-05).

Every metabug carrying a team's whiteboard tag is mirrored as an Epic, empty
or not. New and edited metabugs get theirs through normal sync; this covers
the ones that already exist and have not changed since the team onboarded.
That is a burst of writes at rollout, so this runs **dry by default**: it
prints what it would create and changes nothing until `--apply` is passed.

A team's metabugs are the bugs with the `meta` keyword and the team's tag in
the whiteboard (`[tag]` or `[tag-anything]`, as the runner matches it). If
the action also sets `sync_products_components`, only those are scanned.

Metabugs are found by BMO's `meta` keyword, not by a `[meta]` title prefix;
real metabugs exist without the prefix.

    PYTHONPATH=. uv run python bin/ensure_metabug_epics.py --tag aiplatform            # dry run
    PYTHONPATH=. uv run python bin/ensure_metabug_epics.py --tag aiplatform --apply

Closed metabugs are skipped unless `--include-closed` is given: creating
Epics for trackers resolved years ago only clutters the backlog.
"""

import argparse
import logging
import re
import sys
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from jbi import Operation
from jbi.bugzilla.service import get_service as get_bugzilla_service
from jbi.configuration import get_actions
from jbi.hierarchy import is_metabug, is_open, mirror_issue_key
from jbi.jira import get_service as get_jira_service
from jbi.models import Action, ActionContext, JiraContext

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("ensure_metabug_epics")


def parse_scope(entry: str) -> tuple[str, str | None]:
    """Split a `Product::Component` scope entry."""
    if "::" in entry:
        product, component = entry.split("::", 1)
        return product.strip(), component.strip()
    return entry.strip(), None


def carries_tag(bug, tag: str) -> bool:
    """Same whiteboard match the runner uses to pick an action."""
    pattern = r"\[" + re.escape(tag) + r"(-[^\]]*)*\]"
    return bool(re.search(pattern, bug.whiteboard or "", re.IGNORECASE))


def find_metabugs(
    bugzilla_client,
    tag: str,
    scope: list[str] | None = None,
    active_within_days: int | None = None,
) -> list:
    """Return the team's metabugs: `meta` keyword and the whiteboard tag.

    `active_within_days` filters on `last_change_time`. A long-lived
    component accumulates dormant trackers -- a dry run against a legacy
    component found 52 open metabugs, many untouched since 2015 -- and an
    Epic per dormant tracker is backlog clutter, not planning value.
    """
    from jbi.bugzilla.models import Bug

    base = {
        "keywords": "meta",
        "keywords_type": "allwords",
        "status_whiteboard": f"[{tag}",
        "status_whiteboard_type": "substring",
        "include_fields": "id,summary,status,resolution,keywords,see_also,whiteboard,"
        "product,component,groups,is_private,blocks,depends_on,last_change_time",
    }
    queries = []
    for entry in scope or [None]:
        params = dict(base)
        if entry:
            product, component = parse_scope(entry)
            params["product"] = product
            if component:
                params["component"] = component
        queries.append(params)

    raw_bugs: dict[int, dict] = {}
    for params in queries:
        url = f"{bugzilla_client.base_url}/rest/bug?{urlencode(params)}"
        for raw in bugzilla_client._call("GET", url).get("bugs") or []:
            raw_bugs[raw["id"]] = raw

    cutoff = (
        datetime.now(UTC) - timedelta(days=active_within_days)
        if active_within_days is not None
        else None
    )
    metabugs = []
    for raw in raw_bugs.values():
        if cutoff is not None:
            changed = raw.get("last_change_time")
            try:
                when = datetime.fromisoformat(str(changed).replace("Z", "+00:00"))
            except ValueError:
                continue
            if when < cutoff:
                continue
        bug = Bug.model_validate(raw)
        if carries_tag(bug, tag):
            metabugs.append(bug)
    return sorted(metabugs, key=lambda b: b.id)


def _context(action: Action, metabug, operation, issue=None) -> ActionContext:
    return ActionContext(
        action=action,
        operation=operation,
        bug=metabug,
        event={"action": "scan", "time": "1970-01-01T00:00:00Z"},
        jira=JiraContext(project=action.jira_project_key, issue=issue),
    )


def run(
    action: Action,
    apply: bool,
    include_closed: bool,
    active_within_days: int | None,
) -> int:
    from jbi.hierarchy import ensure_mirror_epic
    from jbi.visibility import bug_restriction_reason

    if not action.parameters.metabug_epics_enabled:
        print(f"action {action.whiteboard_tag!r} has metabug_epics_enabled off")
        return 2

    bugzilla_service = get_bugzilla_service()
    jira_service = get_jira_service()
    project_key = action.jira_project_key

    metabugs = find_metabugs(
        bugzilla_service.client,
        action.whiteboard_tag,
        action.parameters.sync_products_components,
        active_within_days,
    )
    print(f"[{action.whiteboard_tag}]: {len(metabugs)} metabug(s)")

    would_create, already_epic, left_alone, skipped_closed, restricted = (
        [],
        [],
        [],
        [],
        [],
    )
    for metabug in metabugs:
        if not is_metabug(metabug):  # defensive: trust the model, not the query
            continue
        if bug_restriction_reason(metabug):
            restricted.append(metabug.id)
            continue
        if not include_closed and not is_open(metabug):
            skipped_closed.append(metabug.id)
            continue

        existing = mirror_issue_key(metabug, project_key)
        if existing:
            context = _context(action, metabug, Operation.HANDLE, existing)
            issue_type = jira_service.get_issue_type(context, existing)
            if issue_type == "Epic":
                already_epic.append((metabug.id, existing))
            else:
                left_alone.append((metabug.id, existing, issue_type))
            continue

        would_create.append(metabug)

    print()
    print(f"already mirrored by an Epic : {len(already_epic)}")
    print(f"mirrored by a non-Epic      : {len(left_alone)}  (left alone)")
    for bug_id, key, issue_type in left_alone[:10]:
        print(f"    bug {bug_id} -> {key} ({issue_type})")
    print(f"restricted, never mirrored  : {len(restricted)}")
    print(f"closed, skipped             : {len(skipped_closed)}")
    print(f"Epics to create             : {len(would_create)}")
    for metabug in would_create[:20]:
        print(f"    bug {metabug.id}: {(metabug.summary or '')[:60]}")
    if len(would_create) > 20:
        print(f"    ... and {len(would_create) - 20} more")

    if not apply:
        print()
        print("DRY RUN -- nothing was created. Re-run with --apply to create.")
        return 0

    created = 0
    for metabug in would_create:
        # Re-check: an earlier iteration may have created it as someone's
        # outer metabug.
        fresh = bugzilla_service.refresh_bug_data(metabug)
        if mirror_issue_key(fresh, project_key):
            continue
        context = _context(action, fresh, Operation.CREATE)
        key = ensure_mirror_epic(context, fresh, jira_service, bugzilla_service)
        if key:
            created += 1
            print(f"    created {key} for bug {fresh.id}")
    print(f"\ncreated {created} Epic(s)")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="action whiteboard tag")
    parser.add_argument(
        "--apply", action="store_true", help="actually create the Epics"
    )
    parser.add_argument(
        "--active-within-days",
        type=int,
        default=None,
        help="only mirror metabugs changed in the last N days (default: no age filter)",
    )
    parser.add_argument(
        "--include-closed",
        action="store_true",
        help="also mirror resolved metabugs (default: skip them)",
    )
    args = parser.parse_args(argv)

    actions = get_actions()
    action = actions.get(args.tag)
    if action is None:
        print(f"no action with tag {args.tag!r}; known: {', '.join(actions.by_tag)}")
        return 2

    return run(
        action,
        apply=args.apply,
        include_closed=args.include_closed,
        active_within_days=args.active_within_days,
    )


if __name__ == "__main__":
    sys.exit(main())
