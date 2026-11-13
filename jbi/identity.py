"""Resolve the same human in Bugzilla and in Jira (R-11).

The two systems identify people differently: a BMO account effectively *is* an
email address, while Jira Cloud identifies users by an opaque `accountId` whose
associated email may differ from the BMO one, or be hidden entirely.

Resolution is a three-tier cascade, in this order:

1. the YAML override map loaded here,
2. automatic email lookup against Jira (`JiraService.find_jira_user`),
3. a safe fallback -- leave the assignee unset and attribute comments in text.

The order matters: an explicit override is the most trustworthy signal,
automatic email matching handles the common case, and the fallback neither
guesses an identity nor drops information. The map therefore holds *exceptions
only* -- people whose emails genuinely differ, or whose Jira email is hidden --
which is what keeps it from becoming a directory anyone has to hand-maintain.
New hires with a consistent corporate email resolve at tier 2 with no entry.

The Jira side of an entry is keyed on `accountId`, not email, because
`accountId` is stable while a Jira email can change or be hidden.
"""

import functools
import logging
import os
from typing import Mapping, Optional

from pydantic import BaseModel
from pydantic_yaml import parse_yaml_raw_as

from jbi import environment

logger = logging.getLogger(__name__)

# BMO's sentinel for "nobody is assigned". Never a person to look up.
UNASSIGNED_EMAIL = "nobody@mozilla.org"


class IdentityEntry(BaseModel, frozen=True):
    """One person, as known to both systems."""

    bmo_email: str
    jira_account_id: Optional[str] = None
    display_name: Optional[str] = None


class IdentityMap(BaseModel):
    """The override map: exceptions automatic resolution cannot handle."""

    users: list[IdentityEntry] = []

    model_config = {"ignored_types": (functools.cached_property,)}

    @functools.cached_property
    def by_bmo_email(self) -> Mapping[str, IdentityEntry]:
        """Index entries by (lowercased) BMO email."""
        return {entry.bmo_email.lower(): entry for entry in self.users}

    @functools.cached_property
    def by_jira_account_id(self) -> Mapping[str, IdentityEntry]:
        """Index entries by Jira accountId."""
        return {
            entry.jira_account_id: entry
            for entry in self.users
            if entry.jira_account_id
        }

    def jira_account_id_for(self, bmo_email: Optional[str]) -> Optional[str]:
        """Return the mapped Jira accountId for a BMO email, if any."""
        if not bmo_email:
            return None
        entry = self.by_bmo_email.get(bmo_email.lower())
        return entry.jira_account_id if entry else None

    def bmo_email_for(self, jira_account_id: Optional[str]) -> Optional[str]:
        """Return the mapped BMO email for a Jira accountId, if any."""
        if not jira_account_id:
            return None
        entry = self.by_jira_account_id.get(jira_account_id)
        return entry.bmo_email if entry else None

    def display_name_for(self, jira_account_id: Optional[str]) -> Optional[str]:
        """Return the mapped display name for a Jira accountId, if any."""
        if not jira_account_id:
            return None
        entry = self.by_jira_account_id.get(jira_account_id)
        return entry.display_name if entry else None


def get_identity_map_from_file(path: str) -> IdentityMap:
    """Load an identity map from a YAML file.

    A missing file is not an error: the map holds exceptions, and having none
    is the expected state for a deployment where every email matches.
    """
    if not os.path.exists(path):
        logger.info("No identity map at %s, using an empty map", path)
        return IdentityMap()

    with open(path, encoding="utf8") as file:
        return parse_yaml_raw_as(IdentityMap, file.read())


@functools.lru_cache(maxsize=1)
def get_identity_map(env=None) -> IdentityMap:
    """Load the identity map for the current environment."""
    if env is None:
        env = environment.get_settings().env
    return get_identity_map_from_file(f"config/identity_map.{env}.yaml")
