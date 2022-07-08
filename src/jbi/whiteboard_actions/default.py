"""
Default actions is listed below.
`init` is required; and requires at minimum the
`whiteboard_tag` and `jira_project_key`.

`init` should return a __call__able
"""
import logging

from src.app.environment import get_settings
from src.jbi.bugzilla import BugzillaBug, BugzillaWebhookRequest
from src.jbi.errors import ActionError
from src.jbi.services import get_bugzilla, get_jira, getbug_as_bugzilla_object

settings = get_settings()

logger = logging.getLogger(__name__)


def init(whiteboard_tag, jira_project_key, **kwargs):
    """Function that takes required and optional params and returns a callable object"""
    return DefaultExecutor(
        whiteboard_tag=whiteboard_tag, jira_project_key=jira_project_key, **kwargs
    )


class DefaultExecutor:
    """Callable class that encapsulates the default action."""

    def __init__(self, whiteboard_tag, jira_project_key, **kwargs):
        """Initialize DefaultExecutor Object"""
        self.whiteboard_tag = whiteboard_tag
        self.jira_project_key = jira_project_key

        self.bugzilla_client = get_bugzilla()
        self.jira_client = get_jira()

    def __call__(  # pylint: disable=inconsistent-return-statements
        self, payload: BugzillaWebhookRequest
    ):
        """Called from BZ webhook when default action is used. All default-action webhook-events are processed here."""
        target = payload.event.target  # type: ignore
        if target == "comment":
            bug_obj = payload.bug
            return self.comment_create_or_noop(payload=payload, bug_obj=bug_obj)  # type: ignore
        if target == "bug":
            bug_obj = getbug_as_bugzilla_object(payload)
            return self.bug_create_or_update(payload=payload, bug_obj=bug_obj)
        logger.debug(
            "Ignore event target %r",
            target,
            extra={
                "request": payload.json(),
            },
        )

    def comment_create_or_noop(
        self, payload: BugzillaWebhookRequest, bug_obj: BugzillaBug
    ):
        """Confirm issue is already linked, then apply comments; otherwise noop"""
        linked_issue_key = bug_obj.extract_from_see_also()

        log_context = {
            "request": payload.json(),
            "bug": bug_obj.json(),
        }
        if not linked_issue_key:
            logger.debug(
                "No Jira issue linked to Bug %s",
                bug_obj.id,
                extra=log_context,
            )
            return {"status": "noop"}

        jira_response = self.jira_client.issue_add_comment(
            issue_key=linked_issue_key,
            comment=payload.map_as_jira_comment(),
        )
        logger.debug(
            "Comment added to Jira issue %s",
            linked_issue_key,
            extra=log_context,
        )
        return {"status": "comment", "jira_response": jira_response}

    def bug_create_or_update(
        self, payload: BugzillaWebhookRequest, bug_obj: BugzillaBug
    ):  # pylint: disable=too-many-locals
        """Create and link jira issue with bug, or update; rollback if multiple events fire"""
        linked_issue_key = bug_obj.extract_from_see_also()  # type: ignore
        if not linked_issue_key:
            return self.create_and_link_issue(payload, bug_obj)

        log_context = {
            "request": payload.json(),
            "bug": bug_obj.json(),
        }
        logger.debug(
            "Update fields of Jira issue %s for Bug %s",
            linked_issue_key,
            bug_obj.id,
            extra=log_context,
        )
        jira_response_update = self.jira_client.update_issue_field(
            key=linked_issue_key, fields=bug_obj.map_as_jira_issue()
        )

        comments = payload.map_as_comments()
        jira_response_comments = []
        for i, comment in enumerate(comments):
            logger.debug(
                "Create comment #%s on Jira issue %s",
                i + 1,
                linked_issue_key,
                extra=log_context,
            )
            jira_response_comments.append(
                self.jira_client.issue_add_comment(
                    issue_key=linked_issue_key, comment=comment
                )
            )
        return {
            "status": "update",
            "jira_responses": [jira_response_update, jira_response_comments],
        }

    def create_and_link_issue(
        self, payload, bug_obj
    ):  # pylint: disable=too-many-locals
        """create jira issue and establish link between bug and issue; rollback/delete if required"""
        log_context = {
            "request": payload.json(),
            "bug": bug_obj.json(),
        }
        logger.debug(
            "Create new Jira issue for Bug %s",
            bug_obj.id,
            extra=log_context,
        )
        comment_list = self.bugzilla_client.get_comments(idlist=[bug_obj.id])
        fields = {
            **bug_obj.map_as_jira_issue(),  # type: ignore
            "issuetype": {"name": bug_obj.issue_type()},
            "description": comment_list["bugs"][str(bug_obj.id)]["comments"][0]["text"],
            "project": {"key": self.jira_project_key},
        }

        jira_response_create = self.jira_client.create_issue(fields=fields)

        # Jira response can be of the form: List or Dictionary
        if isinstance(jira_response_create, list):
            # if a list is returned, get the first item
            jira_response_create = jira_response_create[0]

        if isinstance(jira_response_create, dict):
            # if a dict is returned or the first item in a list, confirm there are no errors
            if any(
                element in ["errors", "errorMessages"] and jira_response_create[element]
                for element in jira_response_create.keys()
            ):
                raise ActionError(f"response contains error: {jira_response_create}")

        jira_key_in_response = jira_response_create.get("key")
        bug_obj = getbug_as_bugzilla_object(request=payload)
        jira_key_in_bugzilla = bug_obj.extract_from_see_also()
        _duplicate_creation_event = (
            jira_key_in_bugzilla is not None
            and jira_key_in_response != jira_key_in_bugzilla
        )
        if _duplicate_creation_event:
            logger.warning(
                "Delete duplicated Jira issue %s from Bug %s",
                jira_key_in_response,
                bug_obj.id,
                extra=log_context,
            )
            jira_response_delete = self.jira_client.delete_issue(
                issue_id_or_key=jira_key_in_response
            )
            return {"status": "duplicate", "jira_response": jira_response_delete}

        jira_url = f"{settings.jira_base_url}browse/{jira_key_in_response}"
        logger.debug(
            "Link %r on Bug %s",
            jira_url,
            bug_obj.id,
            extra=log_context,
        )
        update = self.bugzilla_client.build_update(see_also_add=jira_url)
        bugzilla_response = self.bugzilla_client.update_bugs([bug_obj.id], update)

        bugzilla_url = f"{settings.bugzilla_base_url}/show_bug.cgi?id={bug_obj.id}"
        logger.debug(
            "Link %r on Jira issue %s",
            bugzilla_url,
            jira_key_in_response,
            extra=log_context,
        )
        jira_response = self.jira_client.create_or_update_issue_remote_links(
            issue_key=jira_key_in_response,
            link_url=bugzilla_url,
            title="Bugzilla Ticket",
        )
        return {
            "status": "create",
            "bugzilla_response": bugzilla_response,
            "jira_response": jira_response,
        }
