#!/usr/bin/env python
"""Eagerly create mirror Epics for the metabugs in a synced component (R-05).

Phase 2 creates a Jira Epic for every in-scope metabug, not only for those
whose children happen to sync. That is a burst of writes at rollout -- a
single pilot metabug we inspected has 47 children, and a component can hold
dozens of metabugs -- so this runs **dry by default**: it prints what it would
create and changes nothing until `--apply` is passed.

Metabugs are found by BMO's `meta` keyword, not by a `[meta]` title prefix;
real metabugs exist without the prefix.

    python bin/ensure_metabug_epics.py --tag aiplatform            # dry run
    python bin/ensure_metabug_epics.py --tag aiplatform --apply

Closed metabugs are skipped unless `--include-closed` is given: creating
Epics for trackers resolved years ago only clutters the backlog.
"""

import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta

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


def find_metabugs(
    bugzilla_client,
    product: str,
    component: str | None,
    active_within_days: int | None = None,
) -> list:
    """Return the metabugs in a Product/Component, via the `meta` keyword.

    `active_within_days` filters on `last_change_time`. A long-lived
    component accumulates dormant trackers -- a dry run against a legacy
    component found 52 open metabugs, many untouched since 2015 -- and an
    Epic per dormant tracker is backlog clutter, not planning value.
    """
    params = {
        "product": product,
        "keywords": "meta",
        "keywords_type": "allwords",
        "include_fields": "id,summary,status,resolution,keywords,see_also,"
        "product,component,blocks,depends_on,last_change_time",
    }
    if component:
        params["component"] = component

    query = "&".join(f"{k}={v}" for k, v in params.items()).replace(" ", "%20")
    url = f"{bugzilla_client.base_url}/rest/bug?{query}"
    payload = bugzilla_client._call("GET", url)
    from jbi.bugzilla.models import Bug

    raw_bugs = payload.get("bugs") or []
    if active_within_days is not None:
        cutoff = datetime.now(UTC) - timedelta(days=active_within_days)
        kept = []
        for raw in raw_bugs:
            changed = raw.get("last_change_time")
            if not changed:
                continue
            try:
                when = datetime.fromisoformat(str(changed).replace("Z", "+00:00"))
            except ValueError:
                continue
            if when >= cutoff:
                kept.append(raw)
        raw_bugs = kept

    return [Bug.model_validate(b) for b in raw_bugs]


def run(
    action: Action,
    apply: bool,
    include_closed: bool,
    active_within_days: int | None,
) -> int:
    scope = action.parameters.sync_products_components
    if not scope:
        print(
            f"action {action.whiteboard_tag!r} has no sync_products_components; "
            "refusing to scan every product"
        )
        return 2

    bugzilla_service = get_bugzilla_service()
    jira_service = get_jira_service()
    project_key = action.jira_project_key

    would_create, already_epic, left_alone, skipped_closed = [], [], [], []

    for entry in scope:
        product, component = parse_scope(entry)
        metabugs = find_metabugs(
            bugzilla_service.client, product, component, active_within_days
        )
        print(f"{entry}: {len(metabugs)} metabug(s) with the `meta` keyword")

        for metabug in metabugs:
            if not is_metabug(metabug):  # defensive: trust the model, not the query
                continue
            if not include_closed and not is_open(metabug):
                skipped_closed.append(metabug.id)
                continue

            existing = mirror_issue_key(metabug, project_key)
            if existing:
                context = ActionContext(
                    action=action,
                    operation=Operation.HANDLE,
                    bug=metabug,
                    event={"action": "scan", "time": "1970-01-01T00:00:00Z"},
                    jira=JiraContext(project=project_key, issue=existing),
                )
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
        context = ActionContext(
            action=action,
            operation=Operation.CREATE,
            bug=metabug,
            event={"action": "scan", "time": "1970-01-01T00:00:00Z"},
            jira=JiraContext(project=project_key),
        )
        from jbi.hierarchy import ensure_mirror_epic

        key = ensure_mirror_epic(context, metabug, jira_service, bugzilla_service)
        if key:
            created += 1
            print(f"    created {key} for bug {metabug.id}")
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
