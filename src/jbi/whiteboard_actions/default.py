"""
Default actions is listed below.
`init` is required; and requires at minimum the
`whiteboard_tag` and `jira_project_key`.

`init` should return a __call__able
"""

from src.app.environment import get_settings
from src.jbi.bugzilla_objects import BugzillaBug, BugzillaWebhookRequest
from src.jbi.model import ActionError
from src.jbi.service import get_bugzilla, get_jira


def init(whiteboard_tag, jira_project_key, **kwargs):
    """Function that takes required and optional params and returns a callable object"""
    return DefaultExecutor(
        whiteboard_tag=whiteboard_tag, jira_project_key=jira_project_key, **kwargs
    )


class DefaultExecutor:
    """Callable class that encapsulates the default action."""

    def __init__(self, **kwargs):
        """Initialize DefaultExecutor Object"""
        self.parameters = kwargs
        self.whiteboard_tag = kwargs.get("whiteboard_tag")
        self.jira_project_key = kwargs.get("jira_project_key")
        self.bugzilla_client = get_bugzilla()
        self.jira_client = get_jira()
        self.settings = get_settings()

    def __call__(  # pylint: disable=too-many-locals,too-many-return-statements,inconsistent-return-statements
        self, payload: BugzillaWebhookRequest
    ):
        """Called from BZ webhook when default action is used. All default-action webhook-events are processed here."""
        target = payload.event.target
        if not payload.bug or not isinstance(payload.bug, BugzillaBug):
            raise ActionError("payload is expected to have bug data")

        if target == "comment":
            self.comment_create_or_noop(payload=payload)
        if target == "bug":
            self.bug_create_or_update(payload=payload)

    def comment_create_or_noop(self, payload: BugzillaWebhookRequest):
        """Confirm issue is already linked, then apply comments; otherwise noop"""
        bug_obj = payload.bug
        linked_issue_key = bug_obj.extract_from_see_also()  # type: ignore

        if not linked_issue_key:
            # noop
            return {"status": "noop"}
        # else
        jira_response = self.jira_client.issue_add_comment(
            issue_key=linked_issue_key,
            comment=payload.map_as_jira_comment(),
        )
        return {"status": "comment", "jira_response": jira_response}

    def bug_create_or_update(
        self, payload: BugzillaWebhookRequest
    ):  # pylint: disable=too-many-locals
        """Create and link jira issue with bug, or update; rollback if multiple events fire"""
        bug_obj = payload.bug
        linked_issue_key = bug_obj.extract_from_see_also()  # type: ignore

        if linked_issue_key:
            # update
            fields, comments = payload.map_as_tuple_of_field_dict_and_comments()
            jira_update_response = self.jira_client.update_issue_field(
                key=linked_issue_key, fields=fields
            )
            # comment
            jira_comment_responses = []
            for comment in comments:
                jira_comment_responses.append(
                    self.jira_client.issue_add_comment(
                        issue_key=linked_issue_key, comment=comment
                    )
                )
            return {
                "status": "update",
                "jira_update_response": jira_update_response,
                "jira_comment_responses": jira_comment_responses,
            }
        # else: create jira issue
        fields = {**bug_obj.get_jira_issue_dict(), "key": self.jira_project_key}  # type: ignore

        response = self.jira_client.create_issue(fields=fields)

        # Jira response can be of the form: List or Dictionary
        if isinstance(response, list):
            # if a list is returned, get the first item
            response = response[0]

        if isinstance(response, dict):
            # if a dict is returned or the first item in a list, confirm there are no errors
            if any(
                element in ["errors", "errorMessages"] and response[element]
                for element in response.keys()
            ):
                raise ActionError(f"response contains error: {response}")

        jira_key_in_response = response.get("key")
        if not payload.bug:
            raise ActionError("payload does not contain bug data")
        current_bug_info = self.bugzilla_client.getbug(payload.bug.id)
        bug_obj = BugzillaBug.parse_obj(current_bug_info.__dict__)
        jira_key_in_bugzilla = bug_obj.extract_from_see_also()
        _duplicate_creation_event = (
            jira_key_in_bugzilla is not None
            and jira_key_in_response != jira_key_in_bugzilla
        )
        if _duplicate_creation_event:
            response = self.jira_client.delete_issue(
                issue_id_or_key=jira_key_in_response
            )
            return {"response": response}
        # else:
        jira_url = self.settings.jira_issue_url.format(jira_key_in_response)
        update = self.bugzilla_client.build_update(see_also_add=jira_url)
        bugzilla_response = self.bugzilla_client.update_bugs([bug_obj.id], update)

        bugzilla_url = self.settings.bugzilla_bug_url.format(bug_obj.id)
        jira_response = self.jira_client.create_or_update_issue_remote_links(
            issue_key=jira_key_in_response,
            link_url=bugzilla_url,
            title="Bugzilla Ticket",
        )
        return {
            "bugzilla_response": bugzilla_response,
            "jira_response": jira_response,
        }
