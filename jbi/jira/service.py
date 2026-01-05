# This import is needed (as of Pyhon 3.11) to enable type checking with modules
# imported under `TYPE_CHECKING`
# https://docs.python.org/3/whatsnew/3.7.html#pep-563-postponed-evaluation-of-annotations
# https://docs.python.org/3/whatsnew/3.11.html#pep-563-may-not-be-the-future
from __future__ import annotations

import concurrent
import json
import logging
from functools import lru_cache
from typing import Any, Iterable, Optional

import requests
from dockerflow import checks
from requests import exceptions as requests_exceptions

from jbi import Operation, environment
from jbi.bugzilla import models as bugzilla_models
from jbi.jira.utils import markdown_to_jira
from jbi.models import ActionContext

from .client import JiraClient, JiraCreateError

settings = environment.get_settings()

logger = logging.getLogger(__name__)

JIRA_REQUIRED_PERMISSIONS = {
    "ADD_COMMENTS",
    "CREATE_ISSUES",
    "DELETE_ISSUES",
    "EDIT_ISSUES",
}


class JiraService:
    """Used by action workflows to perform action-specific Jira tasks"""

    def __init__(self, client) -> None:
        self.client = client

    def fetch_visible_projects(self) -> list[str]:
        """Return list of projects that are visible with the configured Jira credentials"""

        projects = self.client.permitted_projects()
        return [project["key"] for project in projects]

    def get_issue(self, context: ActionContext, issue_key):
        """Return the Jira issue fields or `None` if not found."""
        logger.info("Getting issue %s", issue_key, extra=context.model_dump())
        try:
            response = self.client.get_issue(issue_key)
            logger.info(
                "Received issue %s",
                issue_key,
                extra={"response": response, **context.model_dump()},
            )
            return response
        except requests_exceptions.HTTPError as exc:
            if getattr(exc.response, "status_code", None) != 404:
                raise
            logger.error(
                "Could not read issue %s: %s",
                issue_key,
                exc,
                extra=context.model_dump(),
            )
            return None

    def create_jira_issue(
        self, context: ActionContext, description: str, issue_type: str
    ):
        """Create a Jira issue with basic fields in the project and return its key."""
        bug = context.bug
        fields: dict[str, Any] = {
            "summary": bug.summary,
            "issuetype": {"name": issue_type},
            "description": markdown_to_jira(
                description, max_length=context.action.parameters.jira_char_limit
            ),
            "project": {"key": context.jira.project},
        }
        logger.info(
            "Creating new Jira issue for Bug %s",
            bug.id,
            extra={"fields": fields, **context.model_dump()},
        )
        try:
            response = self.client.create_issue(fields=fields)
        except requests.HTTPError as exc:
            assert exc.response is not None
            try:
                response = exc.response.json()
            except json.JSONDecodeError:
                response = exc.response.text

            logger.exception(
                "Failed to create issue for Bug %s",
                bug.id,
                extra={"response": response, **context.model_dump()},
            )
            raise JiraCreateError(f"Failed to create issue for Bug {bug.id}") from exc

        # Jira response can be of the form: List or Dictionary
        # if a list is returned, get the first item
        issue_data = response[0] if isinstance(response, list) else response
        logger.info(
            "Jira issue %s created for Bug %s",
            issue_data["key"],
            bug.id,
            extra={"response": response, **context.model_dump()},
        )
        return issue_data

    def add_jira_comment(self, context: ActionContext):
        """Publish a comment on the specified Jira issue"""
        context = context.update(operation=Operation.COMMENT)
        commenter = context.event.user.login if context.event.user else "unknown"

        if context.event.target == "attachment":
            routing_key = (
                context.event.routing_key or ".modify"  # only to please type checking.
            )
            _, verb = routing_key.rsplit(".", 1)
            past_verb = {"modify": "modified"}.get(verb, f"{verb}d")
            formatted_comment = f"*{commenter}* {past_verb} an attachment:"

            # When event target is an attachment, the webhook bug object has attachment info.
            if context.bug.attachment:
                att = context.bug.attachment
                formatted_comment += f"\n*Description*: {att.description}"
                formatted_comment += (
                    f"\n*Filename*: {att.file_name} ({att.content_type})"
                )
                if phabricator_url := att.phabricator_url(
                    base_url=settings.phabricator_base_url
                ):
                    formatted_comment += f"\n*Phabricator URL*: {phabricator_url}"

        else:
            comment = context.bug.comment
            assert comment  # See jbi.steps.create_comment()
            assert comment.body  # Also see jbi.steps.create_comment()
            prefix = f"*{commenter}* commented: \n"
            formatted_comment = prefix + markdown_to_jira(
                comment.body,
                max_length=context.action.parameters.jira_char_limit - len(prefix),
            )

        issue_key = context.jira.issue
        jira_response = self.client.issue_add_comment(
            issue_key=issue_key,
            comment=formatted_comment,
        )
        logger.info(
            "User comment added to Jira issue %s",
            issue_key,
            extra=context.model_dump(),
        )
        return jira_response

    def add_jira_comments_for_changes(self, context: ActionContext):
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
            logger.info(
                "Create comment #%s on Jira issue %s",
                i + 1,
                issue_key,
                extra=context.update(operation=Operation.COMMENT).model_dump(),
            )
            jira_response = self.client.issue_add_comment(
                issue_key=issue_key, comment=json.dumps(comment, indent=4)
            )
            jira_response_comments.append(jira_response)

        return jira_response_comments

    def delete_jira_issue_if_duplicate(
        self, context: ActionContext, latest_bug: bugzilla_models.Bug
    ):
        """Rollback the Jira issue creation if there is already a linked Jira issue
        on the Bugzilla ticket"""
        issue_key = context.jira.issue
        jira_key_in_bugzilla = latest_bug.extract_from_see_also(
            project_key=context.jira.project
        )
        _duplicate_creation_event = (
            jira_key_in_bugzilla is not None and issue_key != jira_key_in_bugzilla
        )
        if not _duplicate_creation_event:
            return None

        logger.warning(
            "Delete duplicated Jira issue %s from Bug %s",
            issue_key,
            context.bug.id,
            extra=context.update(operation=Operation.DELETE).model_dump(),
        )
        jira_response_delete = self.client.delete_issue(issue_id_or_key=issue_key)
        return jira_response_delete

    def add_link_to_bugzilla(self, context: ActionContext):
        """Add link to Bugzilla ticket in Jira issue"""
        bug = context.bug
        issue_key = context.jira.issue
        bugzilla_url = f"{settings.bugzilla_base_url}/show_bug.cgi?id={bug.id}"
        logger.info(
            "Link %r on Jira issue %s",
            bugzilla_url,
            issue_key,
            extra=context.update(operation=Operation.LINK).model_dump(),
        )
        icon_url = f"{settings.bugzilla_base_url}/favicon.ico"
        return self.client.create_or_update_issue_remote_links(
            issue_key=issue_key,
            link_url=bugzilla_url,
            title=bugzilla_url,
            icon_url=icon_url,
            icon_title=icon_url,
        )

    def clear_assignee(self, context: ActionContext):
        """Clear the assignee of the specified Jira issue."""
        issue_key = context.jira.issue
        logger.info("Clearing assignee", extra=context.model_dump())
        return self.client.update_issue_field(key=issue_key, fields={"assignee": None})

    def find_jira_user(self, context: ActionContext, email: str):
        """Lookup Jira users, raise an error if not exactly one found."""
        logger.info("Find Jira user with email %s", email, extra=context.model_dump())
        users = self.client.user_find_by_user_string(query=email)
        if len(users) != 1:
            raise ValueError(f"User {email} not found")
        return users[0]

    def assign_jira_user(self, context: ActionContext, email: str):
        """Set the assignee of the specified Jira issue, raise if fails."""
        issue_key = context.jira.issue
        assert issue_key  # Until we have more fine-grained typing of contexts

        jira_user = self.find_jira_user(context, email)
        jira_user_id = jira_user["accountId"]
        try:
            # There doesn't appear to be an easy way to verify that
            # this user can be assigned to this issue, so just try
            # and do it.
            return self.update_issue_field(
                context, "assignee", jira_user_id, wrap_value="accountId"
            )
        except (requests_exceptions.HTTPError, IOError) as exc:
            raise ValueError(
                f"Could not assign {jira_user_id} to issue {issue_key}"
            ) from exc

    def update_issue_field(
        self,
        context: ActionContext,
        field: str,
        value: Any,
        wrap_value: Optional[str] = None,
    ):
        bug = context.bug
        issue_key = context.jira.issue
        logger.info(
            f"Updating {field} of Jira issue %s to %s for Bug %s",
            issue_key,
            value,
            bug.id,
            extra=context.model_dump(),
        )
        fields: dict[str, Any] = {field: {wrap_value: value} if wrap_value else value}
        response = self.client.update_issue_field(key=issue_key, fields=fields)
        logger.info(
            f"Updated {field} of Jira issue %s to %s for Bug %s",
            issue_key,
            value,
            bug.id,
            extra={"response": response, **context.model_dump()},
        )
        return response

    def update_issue_status(self, context: ActionContext, jira_status: str):
        """Update the status of the Jira issue"""
        issue_key = context.jira.issue
        assert issue_key  # Until we have more fine-grained typing of contexts

        kwargs: dict[str, Any] = {}
        if jira_status == "Cancelled":
            kwargs["fields"] = {
                "resolution": {"name": "Invalid"},
            }
            kwargs["update"] = {
                "comment": [{"add": {"body": "Issue was cancelled."}}],
            }

        logger.info(
            "Updating Jira status to %s",
            jira_status,
            extra=context.model_dump(),
        )
        return self.client.set_issue_status(
            issue_key,
            jira_status,
            **kwargs,
        )

    def update_issue_summary(self, context: ActionContext):
        """Update's an issue's summary with the description of an incoming bug"""
        truncated_summary = context.bug.summary or ""

        if len(truncated_summary) > context.action.parameters.jira_char_limit:
            # Truncate on last word.
            truncated_summary = truncated_summary[
                : context.action.parameters.jira_char_limit
            ].rsplit(maxsplit=1)[0]

        return self.update_issue_field(
            context, field="summary", value=truncated_summary
        )

    def update_issue_resolution(self, context: ActionContext, jira_resolution: str):
        """Update the resolution of the Jira issue."""
        return self.update_issue_field(
            context,
            field="resolution",
            value=jira_resolution,
            wrap_value="name",
        )

    def update_issue_components(
        self,
        context: ActionContext,
        components: Iterable[str],
        create_components: bool = False,
    ) -> tuple[Optional[dict], set]:
        """Attempt to add components to the specified issue

        Args:
            issue_key: key of the issues to add the components to
            project: the project key
            components: Component names to add to the issue

        Returns:
            The Jira response (if any), and any components that weren't added
            to the issue because they weren't available on the project
        """
        missing_components = set(components)
        jira_components = []

        all_project_components = self.client.get_project_components(
            context.jira.project
        )
        for comp in all_project_components:
            if comp["name"] in missing_components:
                jira_components.append({"id": comp["id"]})
                missing_components.remove(comp["name"])

        if not jira_components:
            if not create_components:
                return None, missing_components

            for comp_name in missing_components:
                comp_data = {"name": comp_name, "project": context.jira.project}
                new_comp = self.client.create_component(comp_data)
                jira_components.append({"id": new_comp["id"]})
            missing_components.clear()

        resp = self.update_issue_field(
            context, field="components", value=jira_components
        )
        return resp, missing_components

    def update_issue_labels(
        self, issue_key: str, add: Iterable[str], remove: Optional[Iterable[str]]
    ):
        """Update the labels for a specified issue

        Args:
            issue_key: key of the issues to modify the labels on
            add: labels to add
            remove (Optional): labels to remove

        Returns:
            The response from Jira
        """
        if not remove:
            remove = []

        updated_labels = [{"add": label} for label in add] + [
            {"remove": label} for label in remove
        ]
        return self.client.update_issue(
            issue_key=issue_key,
            update={"update": {"labels": updated_labels}},
        )

    def create_issue_link_blocks(
        self, context: ActionContext, blocking_issue: str, blocked_issue: str
    ):
        """Create a 'Blocks' link between two Jira issues.

        Args:
            context: The action context
            blocking_issue: The issue key that blocks (e.g., 'JBI-123')
            blocked_issue: The issue key that is blocked (e.g., 'JBI-456')
        """
        try:
            self.client.create_issue_link(
                data={
                    "type": {"name": "Blocks"},
                    "inwardIssue": {"key": blocked_issue},
                    "outwardIssue": {"key": blocking_issue},
                }
            )
            logger.info(
                "Created link: %s blocks %s",
                blocking_issue,
                blocked_issue,
                extra=context.update(operation=Operation.LINK).model_dump(),
            )
        except requests_exceptions.HTTPError as e:
            # Handle idempotency: 400 error for duplicate links
            if e.response is not None and e.response.status_code == 400:
                logger.info(
                    "Link already exists: %s blocks %s",
                    blocking_issue,
                    blocked_issue,
                    extra=context.model_dump(),
                )
            else:
                raise

    def lookup_jira_issues_for_bug(
        self, context: ActionContext, bug_id: int, bug_data: bugzilla_models.Bug
    ) -> list[str]:
        """Find all Jira issue keys for a Bugzilla bug ID.

        Uses bug_data.extract_from_see_also(project_key=None) to get all issues,
        supporting cross-project linking.

        Args:
            context: The action context
            bug_id: The Bugzilla bug ID
            bug_data: The Bug object containing see_also links

        Returns:
            List of all Jira issue keys found (may be empty)
        """
        jira_keys = bug_data.extract_from_see_also(project_key=None)
        if not jira_keys:
            logger.info(
                "No Jira issues found for bug %s",
                bug_id,
                extra=context.model_dump(),
            )
            return []

        logger.info(
            "Found Jira issues %s for bug %s",
            jira_keys,
            bug_id,
            extra=context.model_dump(),
        )
        return jira_keys

    def check_jira_connection(self):
        try:
            if self.client.get_server_info(True) is None:
                return [checks.Error("Login fails", id="login.fail")]
        except requests.RequestException:
            return [checks.Error("Could not connect to server", id="jira.server.down")]
        return []

    def check_jira_all_projects_are_visible(self, actions):
        # Do not bother executing the rest of checks if connection fails.
        if messages := self.check_jira_connection():
            return messages

        try:
            visible_projects = self.fetch_visible_projects()
        except requests.HTTPError:
            return [
                checks.Error(
                    "Error fetching visible Jira projects", id="jira.visible.error"
                )
            ]

        missing_projects = actions.configured_jira_projects_keys - set(visible_projects)
        if missing_projects:
            return [
                checks.Warning(
                    f"Jira projects {missing_projects} are not visible with configured credentials",
                    id="jira.projects.missing",
                )
            ]

        return []

    def check_jira_all_projects_have_permissions(self, actions):
        """Fetches and validates that required permissions exist for the configured projects"""

        # Do not bother executing the rest of checks if connection fails.
        if messages := self.check_jira_connection():
            return messages

        try:
            projects = self.client.permitted_projects(JIRA_REQUIRED_PERMISSIONS)
        except requests.HTTPError:
            return [
                checks.Error(
                    "Error fetching permitted Jira projects", id="jira.permitted.error"
                )
            ]

        projects_with_required_perms = {project["key"] for project in projects}
        missing_perms = (
            actions.configured_jira_projects_keys - projects_with_required_perms
        )
        if missing_perms:
            missing = ", ".join(missing_perms)
            return [
                checks.Warning(
                    f"Missing permissions for projects {missing}",
                    id="jira.permitted.missing",
                )
            ]

        return []

    def check_jira_all_project_custom_components_exist(self, actions):
        # Do not bother executing the rest of checks if connection fails.

        if messages := self.check_jira_connection():
            return messages

        results = []

        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = {
                executor.submit(self._check_project_components, action): action
                for action in actions
                if action.parameters.jira_components.set_custom_components
            }
            for future in concurrent.futures.as_completed(futures):
                results.extend(future.result())
        return results

    def _check_project_components(self, action):
        project_key = action.parameters.jira_project_key
        specified_components = set(
            action.parameters.jira_components.set_custom_components
        )

        try:
            all_project_components = self.client.get_project_components(project_key)
        except requests.HTTPError:
            return [
                checks.Error(
                    f"Error checking project components for {project_key}",
                    id="jira.components.error",
                )
            ]

        try:
            all_components_names = set(comp["name"] for comp in all_project_components)
        except KeyError:
            return [
                checks.Error(
                    f"Unexpected get_project_components response for {action.whiteboard_tag}",
                    id="jira.components.parsing",
                )
            ]

        unknown = specified_components - all_components_names
        if unknown:
            return [
                checks.Warning(
                    f"Jira project {project_key} does not have components {unknown}",
                    id="jira.components.missing",
                )
            ]

        return []

    def check_jira_all_project_issue_types_exist(self, actions):
        # Do not bother executing the rest of checks if connection fails.

        if messages := self.check_jira_connection():
            return messages

        try:
            paginated_project_response = self.client.paginated_projects(
                expand="issueTypes", keys=sorted(actions.configured_jira_projects_keys)
            )
        except requests.RequestException:
            return [
                checks.Error(
                    "Couldn't fetch projects",
                    id="jira.projects.error",
                )
            ]

        projects = paginated_project_response["values"]
        issue_types_by_project = {
            project["key"]: {issue_type["name"] for issue_type in project["issueTypes"]}
            for project in projects
        }
        missing_issue_types_by_project = {}
        for action in actions:
            action_issue_types = set(action.parameters.issue_type_map.values())
            project_issue_types = issue_types_by_project.get(
                action.jira_project_key, set()
            )
            if missing_issue_types := action_issue_types - project_issue_types:
                missing_issue_types_by_project[action.jira_project_key] = (
                    missing_issue_types
                )
        if missing_issue_types_by_project:
            return [
                checks.Warning(
                    f"Jira projects {set(missing_issue_types_by_project.keys())} with missing issue types",
                    obj=missing_issue_types_by_project,
                    id="jira.types.missing",
                )
            ]
        return []

    def check_jira_pandoc_install(self):
        if markdown_to_jira("- Test") != "* Test":
            return [checks.Error("Pandoc conversion failed", id="jira.pandoc")]
        return []


@lru_cache(maxsize=1)
def get_service():
    """Get atlassian Jira Service"""
    client = JiraClient(
        url=settings.jira_base_url,
        username=settings.jira_username,
        password=settings.jira_api_key,  # package calls this param 'password' but actually expects an api key
        cloud=True,  # we run against an instance of Jira cloud
    )

    return JiraService(client=client)
