"""
Default actions is listed below.
`init` is required; and requires at minimum the
`whiteboard_tag` and `jira_project_key`.

`init` should return a __call__able
"""
from fastapi.responses import JSONResponse

from src.app.environment import get_settings
from src.jbi.bugzilla_objects import BugzillaBug, BugzillaWebhookRequest
from src.jbi.router import ValidationError
from src.jbi.service import get_bugzilla, get_jira


def init(whiteboard_tag, jira_project_key, **kwargs):
    """
    Function that takes required and optional params and returns a callable object
    :param whiteboard_tag: Required param
    :param jira_project_key: Required param
    :param kwargs: Optional params
    :return: DefaultExecutor
    """
    return DefaultExecutor(
        whiteboard_tag=whiteboard_tag, jira_project_key=jira_project_key, **kwargs
    )


class DefaultExecutor:
    """
    Callable class that encapsulates the default action
    """

    def __init__(self, **kwargs):
        """Initialize DefaultExecutor Object"""
        self.parameters = kwargs
        self.whiteboard_tag = kwargs.get("whiteboard_tag")
        self.jira_project_key = kwargs.get("jira_project_key")

    def __call__(  # pylint: disable=too-many-locals,too-many-return-statements,inconsistent-return-statements
        self, payload
    ):
        """
        Called from BZ webhook when default action is used. All default-action webhook-events are processed here.
        """
        try:
            payload: BugzillaWebhookRequest = payload  # typing assistance
            bugzilla_client = get_bugzilla()
            jira_client = get_jira()
            settings = get_settings()

            current_bug_info = bugzilla_client.getbug(payload.bug.id)
            bug_obj = BugzillaBug.parse_obj(current_bug_info.__dict__)
            target = payload.event.target
            linked_issue_key = bug_obj.extract_from_see_also()

            if target == "comment":
                if not linked_issue_key:
                    # noop
                    return JSONResponse(content={"status": "noop"}, status_code=201)
                # else
                jira_response = jira_client.issue_add_comment(
                    issue_key=linked_issue_key,
                    comment=payload.map_as_jira_comment(),
                )
                return JSONResponse(
                    content={"status": "comment", "jira_response": jira_response},
                    status_code=201,
                )

            if target == "bug":
                if linked_issue_key:
                    # update
                    fields, comments = payload.map_as_tuple_of_field_dict_and_comments()
                    fields["issue_key"] = linked_issue_key
                    jira_update_response = jira_client.issue_create_or_update(
                        fields=fields
                    )
                    # comment
                    jira_comment_responses = []
                    for comment in comments:
                        jira_comment_responses.append(
                            jira_client.issue_add_comment(
                                issue_key=linked_issue_key, comment=comment
                            )
                        )
                    return JSONResponse(
                        content={
                            "status": "update",
                            "jira_update_response": jira_update_response,
                            "jira_comment_responses": jira_comment_responses,
                        },
                        status_code=201,
                    )
                # else: create jira issue
                response = jira_client.create_issue(
                    fields=bug_obj.get_jira_issue_dict(
                        jira_project_key=self.jira_project_key
                    )
                )

                if isinstance(response, list):
                    response = response[0]

                if isinstance(response, dict):
                    if any(
                        element in ["errors", "errorMessages"] and response[element]
                        for element in response.keys()
                    ):
                        # Failure to create: err keys exist with value
                        return None

                jira_key_in_response = response.get("key")
                current_bug_info = bugzilla_client.getbug(payload.bug.id)
                bug_obj = BugzillaBug.parse_obj(current_bug_info.__dict__)
                jira_key_in_bugzilla = bug_obj.extract_from_see_also()
                _duplicate_creation_event = (
                    jira_key_in_bugzilla is not None
                    and jira_key_in_response != jira_key_in_bugzilla
                )
                if not _duplicate_creation_event:
                    jira_url = f"{settings.jira_base_url}/browse/{jira_key_in_response}"
                    update = bugzilla_client.build_update(see_also_add=jira_url)
                    bugzilla_response = bugzilla_client.update_bugs(
                        [bug_obj.id], update
                    )

                    bugzilla_url = (
                        f"{settings.bugzilla_base_url}show_bug.cgi?id={bug_obj.id}"
                    )
                    jira_response = jira_client.create_or_update_issue_remote_links(
                        issue_key=jira_key_in_response,
                        link_url=bugzilla_url,
                        title="Bugzilla Ticket",
                    )
                    content = {
                        "bugzilla_response": bugzilla_response,
                        "jira_response": jira_response,
                    }
                    return JSONResponse(content=content, status_code=201)
                # else:
                # TODO: ROLLBACK # pylint: disable=fixme
                return JSONResponse()
        except ValidationError as exception:
            return JSONResponse(content={"error": exception}, status_code=201)
