"""Contains a Jira REST client and functions comprised of common operations
with that REST client
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from atlassian import Jira, errors

from jbi import Operation, environment
from jbi.models import ActionContext, BugzillaWebhookComment

from .common import InstrumentedClient, ServiceHealth

if TYPE_CHECKING:
    from jbi.models import Actions

settings = environment.get_settings()

logger = logging.getLogger(__name__)


JIRA_DESCRIPTION_CHAR_LIMIT = 32767


@lru_cache(maxsize=1)
def get_client():
    """Get atlassian Jira Service"""
    jira_client = Jira(
        url=settings.jira_base_url,
        username=settings.jira_username,
        password=settings.jira_api_key,  # package calls this param 'password' but actually expects an api key
        cloud=True,  # we run against an instance of Jira cloud
    )

    return InstrumentedClient(
        wrapped=jira_client,
        prefix="jira",
        methods=(
            "update_issue_field",
            "set_issue_status",
            "issue_add_comment",
            "create_issue",
        ),
        exceptions=(errors.ApiError,),
    )


def fetch_visible_projects() -> list[dict]:
    """Return list of projects that are visible with the configured Jira credentials"""
    client = get_client()
    projects: list[dict] = client.projects(included_archived=None)
    return projects


def check_health(actions: Actions) -> ServiceHealth:
    """Check health for Jira Service"""
    client = get_client()
    server_info = client.get_server_info(True)
    is_up = server_info is not None
    health: ServiceHealth = {
        "up": is_up,
        "all_projects_are_visible": is_up and _all_projects_visible(actions),
        "all_projects_have_permissions": _all_projects_permissions(actions),
    }
    return health


def _all_projects_visible(actions: Actions) -> bool:
    visible_projects = {project["key"] for project in fetch_visible_projects()}
    missing_projects = actions.configured_jira_projects_keys - visible_projects
    if missing_projects:
        logger.error(
            "Jira projects %s are not visible with configured credentials",
            missing_projects,
        )
    return not missing_projects


def _all_projects_permissions(actions: Actions):
    """Fetches and validates that required permissions exist for the configured projects"""
    all_projects_perms = _fetch_project_permissions(actions)
    return _validate_permissions(all_projects_perms)


def _fetch_project_permissions(actions):
    """Fetches permissions for the configured projects"""
    required_perms_by_project = {
        action.parameters["jira_project_key"]: action.required_jira_permissions
        for action in actions
        if "jira_project_key" in action.parameters
    }
    client = get_client()
    all_projects_perms = {}
    # Query permissions for all configured projects in parallel threads.
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures_to_projects = {
            executor.submit(
                client.get_permissions,
                project_key=project_key,
                permissions=",".join(required_permissions),
            ): project_key
            for project_key, required_permissions in required_perms_by_project.items()
        }
        # Obtain futures' results unordered.
        for future in concurrent.futures.as_completed(futures_to_projects):
            project_key = futures_to_projects[future]
            response = future.result()
            all_projects_perms[project_key] = (
                required_perms_by_project[project_key],
                response["permissions"],
            )
    return all_projects_perms


def _validate_permissions(all_projects_perms):
    """Validates permissions for the configured projects"""
    misconfigured = []
    for project_key, (required_perms, obtained_perms) in all_projects_perms.items():
        missing = required_perms - set(obtained_perms.keys())
        not_given = set(
            entry["key"]
            for entry in obtained_perms.values()
            if not entry["havePermission"]
        )
        if missing | not_given:
            misconfigured.append((project_key, missing | not_given))
    for project_key, missing in misconfigured:
        logger.error(
            "Configured credentials don't have permissions %s on Jira project %s",
            ",".join(missing),
            project_key,
            extra={
                "jira": {
                    "project": project_key,
                }
            },
        )
    return not misconfigured


class JiraCreateError(Exception):
    """Error raised on Jira issue creation."""


def create_jira_issue(
    context: ActionContext,
    description: str,
    sync_whiteboard_labels: bool,
) -> str:
    """Create a Jira issue with the specified fields and return its key."""
    bug = context.bug
    logger.debug(
        "Create new Jira issue for Bug %s",
        bug.id,
        extra=context.dict(),
    )
    fields: dict[str, Any] = {
        "summary": bug.summary,
        "issuetype": {"name": bug.issue_type()},
        "description": description[:JIRA_DESCRIPTION_CHAR_LIMIT],
        "project": {"key": context.jira.project},
    }
    if sync_whiteboard_labels:
        fields["labels"] = bug.get_jira_labels()

    jira_response_create = get_client().create_issue(fields=fields)

    # Jira response can be of the form: List or Dictionary
    if isinstance(jira_response_create, list):
        # if a list is returned, get the first item
        jira_response_create = jira_response_create[0]

    if isinstance(jira_response_create, dict):
        # if a dict is returned or the first item in a list, confirm there are no errors
        errs = ",".join(jira_response_create.get("errors", []))
        msgs = ",".join(jira_response_create.get("errorMessages", []))
        if errs or msgs:
            raise JiraCreateError(errs + msgs)

    issue_key: str = jira_response_create.get("key")
    return issue_key


def update_jira_issue(context: ActionContext, sync_whiteboard_labels):
    """Update the fields of an existing Jira issue"""
    bug = context.bug
    issue_key = context.jira.issue
    logger.debug(
        "Update fields of Jira issue %s for Bug %s",
        issue_key,
        bug.id,
        extra=context.dict(),
    )
    fields: dict[str, Any] = {
        "summary": bug.summary,
    }
    if sync_whiteboard_labels:
        fields["labels"] = bug.get_jira_labels()

    jira_response_update = get_client().update_issue_field(key=issue_key, fields=fields)
    return jira_response_update


def add_jira_comment(
    context: ActionContext,
    comment: BugzillaWebhookComment,
    commenter: str,
):
    """Publish a comment on the specified Jira issue"""
    issue_key = context.jira.issue
    formatted_comment = f"*({commenter})* commented: \n{{quote}}{comment.body}{{quote}}"
    jira_response = get_client().issue_add_comment(
        issue_key=issue_key,
        comment=formatted_comment,
    )
    logger.debug(
        "User comment added to Jira issue %s",
        issue_key,
        extra=context.dict(),
    )
    return jira_response


def add_jira_comments_for_changes(context: ActionContext):
    """Add comments on the specified Jira issue for each change of the event"""
    bug = context.bug
    event = context.event
    issue_key = context.jira.issue

    comments: list = []
    user = event.user.login if event.user else "unknown"
    for change in event.changes or []:
        if change.field in ["status", "resolution"]:
            comments.append(
                {
                    "modified by": user,
                    "resolution": bug.resolution,
                    "status": bug.status,
                }
            )
        if change.field in ["assigned_to", "assignee"]:
            comments.append({"assignee": bug.assigned_to})

    jira_response_comments = []
    for i, comment in enumerate(comments):
        logger.debug(
            "Create comment #%s on Jira issue %s",
            i + 1,
            issue_key,
            extra=context.update(operation=Operation.COMMENT).dict(),
        )
        jira_response = get_client().issue_add_comment(
            issue_key=issue_key, comment=json.dumps(comment, indent=4)
        )
        jira_response_comments.append(jira_response)

    return jira_response_comments


def delete_jira_issue_if_duplicate(context: ActionContext):
    """Rollback the Jira issue creation if there is already a linked Jira issue
    on the Bugzilla ticket"""
    bug = context.bug
    issue_key = context.jira.issue
    # In the time taken to create the Jira issue the bug may have been updated so
    # re-retrieve it to ensure we have the latest data.
    jira_key_in_bugzilla = bug.extract_from_see_also()
    _duplicate_creation_event = (
        jira_key_in_bugzilla is not None and issue_key != jira_key_in_bugzilla
    )
    if not _duplicate_creation_event:
        return None

    logger.warning(
        "Delete duplicated Jira issue %s from Bug %s",
        issue_key,
        bug.id,
        extra=context.update(operation=Operation.DELETE).dict(),
    )
    jira_response_delete = get_client().delete_issue(issue_id_or_key=issue_key)
    return jira_response_delete


def add_link_to_bugzilla(context: ActionContext):
    """Add link to Bugzilla ticket in Jira issue"""
    bug = context.bug
    issue_key = context.jira.issue
    bugzilla_url = f"{settings.bugzilla_base_url}/show_bug.cgi?id={bug.id}"
    logger.debug(
        "Link %r on Jira issue %s",
        bugzilla_url,
        issue_key,
        extra=context.update(operation=Operation.LINK).dict(),
    )
    icon_url = f"{settings.bugzilla_base_url}/favicon.ico"
    return get_client().create_or_update_issue_remote_links(
        issue_key=issue_key,
        link_url=bugzilla_url,
        title=bugzilla_url,
        icon_url=icon_url,
        icon_title=icon_url,
    )


def clear_assignee(context: ActionContext):
    """Clear the assignee of the specified Jira issue."""
    issue_key = context.jira.issue
    logger.debug("Clearing assignee", extra=context.dict())
    return get_client().update_issue_field(key=issue_key, fields={"assignee": None})


def find_jira_user(context: ActionContext, email: str):
    """Lookup Jira users, raise an error if not exactly one found."""
    logger.debug("Find Jira user with email %s", email, extra=context.dict())
    users = get_client().user_find_by_user_string(query=email)
    if len(users) != 1:
        raise ValueError(f"User {email} not found")
    return users[0]


def assign_jira_user(context: ActionContext, email: str):
    """Set the assignee of the specified Jira issue, raise if fails."""
    issue_key = context.jira.issue
    assert issue_key  # Until we have more fine-grained typing of contexts

    jira_user = find_jira_user(context, email)
    jira_user_id = jira_user["accountId"]
    try:
        # There doesn't appear to be an easy way to verify that
        # this user can be assigned to this issue, so just try
        # and do it.
        return get_client().update_issue_field(
            key=issue_key,
            fields={"assignee": {"accountId": jira_user_id}},
        )
    except IOError as exc:
        raise ValueError(
            f"Could not assign {jira_user_id} to issue {issue_key}"
        ) from exc


def update_issue_status(context: ActionContext, jira_status: str):
    """Update the status of the Jira issue"""
    issue_key = context.jira.issue
    assert issue_key  # Until we have more fine-grained typing of contexts

    logger.debug(
        "Updating Jira status to %s",
        jira_status,
        extra=context.dict(),
    )
    return get_client().set_issue_status(
        issue_key,
        jira_status,
    )


def update_issue_resolution(context: ActionContext, jira_resolution: str):
    """Update the resolution of the Jira issue."""
    issue_key = context.jira.issue
    assert issue_key  # Until we have more fine-grained typing of contexts

    logger.debug(
        "Updating Jira resolution to %s",
        jira_resolution,
        extra=context.dict(),
    )
    return get_client().update_issue_field(
        key=issue_key,
        fields={"resolution": jira_resolution},
    )
