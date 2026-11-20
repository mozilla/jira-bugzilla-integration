"""Whether a bug falls inside an action's configured sync scope (R-01).

Extracted from `jbi.runner` so the hierarchy code can ask the same question
without importing the runner (which imports the steps that would import it
back). One definition, two callers.
"""

from typing import Optional

from jbi.bugzilla.models import Bug


def bug_in_scope(bug: Bug, scope: Optional[list[str]]) -> bool:
    """Return True when the bug's Product/Component is in `scope`.

    Entries are either a full ``Product::Component`` pair or a bare
    ``Product`` (matching every component of it). Comparison is
    case-insensitive because BMO product and component names are display
    strings, not identifiers -- but it is *not* punctuation-insensitive, so
    `On Device` and `On-Device` are different components.

    An unset (``None``) scope means "no restriction", which is today's
    behavior.
    """
    if scope is None:
        return True

    product = (bug.product or "").strip().lower()
    product_component = bug.product_component.strip().lower()
    for entry in scope:
        normalized = entry.strip().lower()
        if normalized == product_component or normalized == product:
            return True
    return False
