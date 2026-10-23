#!/usr/bin/env python
"""Seed and drift-check the identity override map (R-11).

The map exists so a handful of people whose Bugzilla and Jira identities do
not match can still be resolved. Nobody should be typing opaque Jira
`accountId` values into YAML by hand, so this script does two jobs:

**Seed** -- given a list of Bugzilla emails, look each one up in Jira and
print the entries to paste into `config/identity_map.{env}.yaml`. Emails that
resolve automatically are reported and skipped: they do not belong in the map,
because tier 2 of the cascade already handles them.

**Drift-check** (`--check`) -- re-validate the accountIds already in the map
and report any that Jira no longer knows about, which is how a stale entry
gets noticed instead of silently assigning nobody.

Usage:
    python bin/seed_identity_map.py --check
    python bin/seed_identity_map.py person@mozilla.com other@mozilla.com
    python bin/seed_identity_map.py --emails-from emails.txt
"""

import argparse
import sys

from jbi import environment
from jbi.identity import get_identity_map
from jbi.jira import get_service as get_jira_service


def find_account(jira_client, email: str) -> list[dict]:
    """Return the Jira users matching an email, via the user search."""
    return jira_client.user_find_by_user_string(query=email) or []


def seed(emails: list[str]) -> int:
    """Print map entries for the emails Jira cannot resolve on its own."""
    jira_client = get_jira_service().client
    identity_map = get_identity_map()

    entries: list[str] = []
    for email in emails:
        if identity_map.jira_account_id_for(email):
            print(f"# {email}: already in the map, skipping", file=sys.stderr)
            continue

        users = find_account(jira_client, email)
        if len(users) == 1:
            # Tier 2 resolves this person; an entry would be dead weight.
            print(
                f"# {email}: resolves automatically, no entry needed", file=sys.stderr
            )
            continue

        if not users:
            print(
                f"# {email}: NOT FOUND in Jira -- needs a manual accountId",
                file=sys.stderr,
            )
            entries.append(
                f"  - bmo_email: {email}\n"
                f"    jira_account_id:  # TODO: fill in\n"
                f"    display_name:  # TODO: fill in"
            )
            continue

        print(
            f"# {email}: {len(users)} Jira users match, pick one",
            file=sys.stderr,
        )
        for user in users:
            print(
                f"#   {user.get('accountId')} {user.get('displayName')} "
                f"{user.get('emailAddress')}",
                file=sys.stderr,
            )
        entries.append(
            f"  - bmo_email: {email}\n"
            f"    jira_account_id: {users[0].get('accountId')}\n"
            f"    display_name: {users[0].get('displayName')}"
        )

    if entries:
        print("users:")
        print("\n".join(entries))
    return 0


def check() -> int:
    """Report entries whose Jira accountId no longer resolves."""
    jira_client = get_jira_service().client
    identity_map = get_identity_map()

    stale = []
    for entry in identity_map.users:
        if not entry.jira_account_id:
            stale.append((entry.bmo_email, "no jira_account_id set"))
            continue
        users = find_account(jira_client, entry.bmo_email)
        account_ids = {user.get("accountId") for user in users}
        if entry.jira_account_id not in account_ids and users:
            stale.append(
                (entry.bmo_email, f"accountId {entry.jira_account_id} not found")
            )

    env = environment.get_settings().env
    if not stale:
        print(f"identity_map.{env}.yaml: {len(identity_map.users)} entries, all OK")
        return 0

    print(f"identity_map.{env}.yaml: {len(stale)} entries need attention")
    for email, reason in stale:
        print(f"  {email}: {reason}")
    return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("emails", nargs="*", help="BMO emails to look up")
    parser.add_argument(
        "--emails-from", help="file with one BMO email per line", default=None
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the accountIds already in the map",
    )
    args = parser.parse_args(argv)

    if args.check:
        return check()

    emails = list(args.emails)
    if args.emails_from:
        with open(args.emails_from, encoding="utf8") as file:
            emails += [line.strip() for line in file if line.strip()]

    if not emails:
        parser.error("provide emails, --emails-from, or --check")

    return seed(emails)


if __name__ == "__main__":
    sys.exit(main())
