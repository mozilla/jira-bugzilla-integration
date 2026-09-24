"""Map BMO metabug relationships onto Jira's epic hierarchy (R-05/R-06).

The two models genuinely disagree, and this module encodes the resolution
settled in plan section 10.1 rather than leaving each caller to re-derive it.

**BMO owns membership, Jira owns the parent.** A bug can block any number of
metabugs; a Jira issue has exactly one parent. So the full many-to-many graph
is mirrored losslessly as Jira *issue links* -- which `steps.sync_dependencies`
already does, via `blocks`/`depends_on` -- and the single Jira parent is a
planning overlay JBI seeds once, at task creation, and never touches again.

Directions, confirmed against live data: a child bug lists its metabugs in
`blocks`, and the metabug lists its children in `depends_on`. Metabugs are
identified by the `meta` **keyword**, not by a `[meta]` title prefix -- real
metabugs exist without the prefix.
"""

import logging
from typing import TYPE_CHECKING, Optional

from jbi.bugzilla.models import Bug
from jbi.environment import get_settings
from jbi.scope import bug_in_scope

if TYPE_CHECKING:
    from jbi.bugzilla.service import BugzillaService
    from jbi.jira import JiraService
    from jbi.models import ActionContext, ActionParams

logger = logging.getLogger(__name__)

# BMO's convention for "this bug tracks other bugs".
METABUG_KEYWORD = "meta"


def is_metabug(bug: Bug) -> bool:
    """Return True when the bug carries BMO's `meta` keyword."""
    return METABUG_KEYWORD in [str(k).lower() for k in (bug.keywords or [])]


def is_open(bug: Bug) -> bool:
    """Return True when the bug is not in a resolved/closed state.

    Used to keep the eager Epic scan from creating Epics for metabugs that
    were closed years ago.
    """
    return not (bug.resolution or "").strip() and (bug.status or "") not in (
        "RESOLVED",
        "VERIFIED",
        "CLOSED",
    )


def candidate_metabug_ids(bug: Bug) -> list[int]:
    """Return the bug ids that *might* be metabugs of this bug, lowest first.

    A child bug's metabugs are the bugs it blocks. Sorted so that the
    tie-break in `choose_parent_metabug` is deterministic and re-runs do not
    flap the parent.
    """
    return sorted(bug.blocks or [])


def resolve_metabugs(
    bug: Bug,
    parameters: "ActionParams",
    bugzilla_service: "BugzillaService",
) -> list[Bug]:
    """Fetch the bug's in-scope metabugs, lowest bug id first.

    Filtered to the action's own sync scope: a metabug in a component JBI
    does not sync has no mirror to parent anything under.
    """
    candidate_ids = candidate_metabug_ids(bug)
    if not candidate_ids:
        return []

    fetched = bugzilla_service.get_bugs_by_ids(candidate_ids)
    metabugs = [
        candidate
        for candidate in fetched.values()
        if is_metabug(candidate)
        and bug_in_scope(candidate, parameters.sync_products_components)
    ]
    return sorted(metabugs, key=lambda candidate: candidate.id)


def mirror_issue_key(metabug: Bug, project_key: str) -> Optional[str]:
    """Return the metabug's existing Jira issue in this project, if any."""
    key = metabug.extract_from_see_also(project_key=project_key)
    return key if isinstance(key, str) else None


def ensure_mirror_epic(
    context: "ActionContext",
    metabug: Bug,
    jira_service: "JiraService",
    bugzilla_service: "BugzillaService",
) -> Optional[str]:
    """Return the Epic key mirroring this metabug, creating it if needed.

    Returns `None` when the metabug is already mirrored by a Jira issue that
    is *not* an Epic. That is the common case at rollout: metabugs synced
    under the current one-directional behavior became Tasks (issue type comes
    from the bug's `type`), and Jira generally refuses a hierarchy-level
    change. Those mirrors are left exactly as they are -- children link to
    them instead of being parented under them -- so nothing existing is
    disturbed and Invariant A still holds: one Jira issue per bug.
    """
    project_key = context.jira.project
    existing = mirror_issue_key(metabug, project_key)

    if existing:
        issue_type = jira_service.get_issue_type(context, existing)
        if issue_type == "Epic":
            return existing
        logger.info(
            "Metabug %s is already mirrored by %s (%s, not an Epic); "
            "leaving it alone and linking instead of parenting",
            metabug.id,
            existing,
            issue_type,
            extra=context.model_dump(),
        )
        return None

    created = jira_service.create_epic(
        context, summary=metabug.summary or f"Bug {metabug.id}", project_key=project_key
    )
    epic_key = created.get("key")
    if not epic_key:
        return None

    # Record the link on the metabug, so the next run finds this Epic instead
    # of creating a second one.
    settings = get_settings()
    bugzilla_service.add_link_to_see_also(
        metabug, f"{settings.jira_base_url}browse/{epic_key}"
    )
    return str(epic_key)


def choose_parent_metabug(metabugs: list[Bug]) -> Optional[Bug]:
    """Pick which metabug's Epic seeds the Jira parent.

    Lowest bug id wins. It is arbitrary but deterministic, so a re-sync does
    not move the parent around; every other metabug is still represented as
    an issue link, so no membership is lost.
    """
    return metabugs[0] if metabugs else None
