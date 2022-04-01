"""
Default actions is listed below.
`init` is required; and requires at minimum the
`whiteboard_tag` and `jira_project_key`.

`init` should return a __call__able
"""

from src.app.environment import get_settings
from src.jbi.bugzilla import BugzillaWebhookRequest
from src.jbi.errors import ActionError
from src.jbi.services import get_bugzilla, get_jira, getbug_as_bugzilla_object


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

    def __call__(  # pylint: disable=inconsistent-return-statements
        self, payload: BugzillaWebhookRequest
    ):
        """Called from BZ webhook when default action is used. All default-action webhook-events are processed here."""
        target = payload.event.target  # type: ignore
        if target == "comment":
            return self.comment_create_or_noop(payload=payload)
        if target == "bug":
            return self.bug_create_or_update(payload=payload)

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
        linked_issue_key = payload.bug.extract_from_see_also()  # type: ignore
        if linked_issue_key:
            # update
            fields, comments = payload.map_as_tuple_of_field_dict_and_comments()
            jira_response_update = self.jira_client.update_issue_field(
                key=linked_issue_key, fields=fields
            )
            # comment
            jira_response_comments = []
            for comment in comments:
                jira_response_comments.append(
                    self.jira_client.issue_add_comment(
                        issue_key=linked_issue_key, comment=comment
                    )
                )
            return {
                "status": "update",
                "jira_responses": [jira_response_update, jira_response_comments],
            }
        # else: create jira issue
        return self.create_and_link_issue(payload)

    def create_and_link_issue(self, payload):
        """create jira issue and establish link between bug and issue; rollback/delete if required"""
        fields = {**payload.bug.map_as_jira_issue(), "project": {"key": self.jira_project_key}}  # type: ignore

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
            jira_response_delete = self.jira_client.delete_issue(
                issue_id_or_key=jira_key_in_response
            )
            return {"status": "duplicate", "jira_response": jira_response_delete}
        # else:
        jira_url = self.settings.jira_issue_url % jira_key_in_response
        update = self.bugzilla_client.build_update(see_also_add=jira_url)
        bugzilla_response = self.bugzilla_client.update_bugs([bug_obj.id], update)

        bugzilla_url = self.settings.bugzilla_bug_url % bug_obj.id
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
