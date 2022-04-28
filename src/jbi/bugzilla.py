"""
Bugzilla Typed Objects for ease of use throughout JBI
View additional bugzilla webhook documentation here: https://bugzilla.mozilla.org/page.cgi?id=webhooks.html

"""
import datetime
import json
import logging
import traceback
from typing import Dict, List, Optional, Tuple
from urllib.parse import ParseResult, urlparse

from pydantic import BaseModel  # pylint: disable=no-name-in-module

bugzilla_logger = logging.getLogger("src.jbi.bugzilla")


class BugzillaWebhookUser(BaseModel):
    """Bugzilla User Object"""

    id: int
    login: str
    real_name: str


class BugzillaWebhookEventChange(BaseModel):
    """Bugzilla Change Object"""

    field: str
    removed: str
    added: str


class BugzillaWebhookEvent(BaseModel):
    """Bugzilla Event Object"""

    action: str
    time: Optional[datetime.datetime]
    user: Optional[BugzillaWebhookUser]
    changes: Optional[List[BugzillaWebhookEventChange]]
    target: Optional[str]
    routing_key: Optional[str]


class BugzillaWebhookAttachment(BaseModel):
    """Bugzilla Attachment Object"""

    content_type: Optional[str]
    creation_time: Optional[datetime.datetime]
    description: Optional[str]
    file_name: Optional[str]
    flags: Optional[List]
    id: int
    is_obsolete: Optional[bool]
    is_patch: Optional[bool]
    is_private: Optional[bool]
    last_change_time: Optional[datetime.datetime]


class BugzillaWebhookComment(BaseModel):
    """Bugzilla Comment Object"""

    body: Optional[str]
    id: Optional[int]
    number: Optional[int]
    is_private: Optional[bool]
    creation_time: Optional[datetime.datetime]

    def is_comment_description(self) -> bool:
        """Used to determine if `self` is a description or comment."""
        return self.number == 0

    def is_comment_generic(self) -> bool:
        """All comments after comment-0 are generic"""
        is_description = self.is_comment_description()
        return not is_description

    def is_private_comment(self) -> bool:
        """Helper function to determine if this comment private--not accessible or open"""
        return bool(self.is_private)


class BugzillaBug(BaseModel):
    """Bugzilla Bug Object"""

    id: int
    is_private: Optional[bool]
    type: Optional[str]
    product: Optional[str]
    component: Optional[str]
    whiteboard: Optional[str]
    keywords: Optional[List]
    flags: Optional[List]
    status: Optional[str]
    resolution: Optional[str]
    see_also: Optional[List]
    summary: Optional[str]
    severity: Optional[str]
    priority: Optional[str]
    creator: Optional[str]
    assigned_to: Optional[str]
    comment: Optional[BugzillaWebhookComment]

    def get_whiteboard_as_list(self) -> List[str]:
        """Convert string whiteboard into list, splitting on ']' and removing '['."""
        if self.whiteboard is not None:
            split_list = self.whiteboard.replace("[", "").split("]")
            return [x.strip() for x in split_list if x not in ["", " "]]
        return []

    def get_whiteboard_with_brackets_as_list(self) -> List[str]:
        """Convert string whiteboard into list, splitting on ']' and removing '['; then re-adding."""
        wb_list = self.get_whiteboard_as_list()
        if wb_list is not None and len(wb_list) > 0:
            return [f"[{element}]" for element in wb_list]
        return []

    def get_jira_labels(self) -> List[str]:
        """
        whiteboard labels are added as a convenience for users to search in jira;
        bugzilla is an expected label in Jira
        since jira-labels can't contain a " ", convert to "."
        """
        wb_list = [wb.replace(" ", ".") for wb in self.get_whiteboard_as_list()]
        wb_bracket_list = [
            wb.replace(" ", ".") for wb in self.get_whiteboard_with_brackets_as_list()
        ]

        return ["bugzilla"] + wb_list + wb_bracket_list

    def get_potential_whiteboard_config_list(self) -> List[str]:
        """Get all possible whiteboard_tag configuration values"""
        converted_list: List = []
        for whiteboard in self.get_whiteboard_as_list():
            converted_tag = self.convert_whiteboard_to_tag(whiteboard=whiteboard)
            if converted_tag not in [None, "", " "]:
                converted_list.append(converted_tag)

        return converted_list

    def convert_whiteboard_to_tag(self, whiteboard):  # pylint: disable=no-self-use
        """Extract tag from whiteboard label"""
        _exists = whiteboard not in [" ", ""]
        if not _exists:
            return ""
        return whiteboard.split(sep="-", maxsplit=1)[0].lower()

    def map_as_jira_issue(self) -> Dict:
        """Extract bug info as jira issue dictionary"""
        type_map: dict = {"enhancement": "Task", "task": "Task", "defect": "Bug"}
        return {
            "summary": self.summary,
            "labels": self.get_jira_labels(),
            "issuetype": {"name": type_map.get(self.type, "Task")},
        }

    def extract_from_see_also(self):
        """Extract Jira Issue Key from see_also if jira url present"""
        if not self.see_also and len(self.see_also) > 0:
            return None

        for url in self.see_also:  # pylint: disable=not-an-iterable
            try:
                parsed_url: ParseResult = urlparse(url=url)
                expected_hosts = ["jira", "atlassian"]

                if any(  # pylint: disable=use-a-generator
                    [part in expected_hosts for part in parsed_url.hostname.split(".")]
                ):
                    parsed_jira_key = parsed_url.path.split("/")[-1]
                    return parsed_jira_key
            except Exception:  # pylint: disable=broad-except
                # Try parsing all see_also fields; log errors.
                bugzilla_logger.debug(traceback.format_exc())
        return None


class BugzillaWebhookRequest(BaseModel):
    """Bugzilla Webhook Request Object"""

    webhook_id: int
    webhook_name: str
    event: BugzillaWebhookEvent
    bug: Optional[BugzillaBug]

    def map_as_jira_comment(self):
        """Extract comment from Webhook Event"""
        comment: BugzillaWebhookComment = self.bug.comment
        commenter: BugzillaWebhookUser = self.event.user
        comment_body: str = comment.body
        body = f"*({commenter.login})* commented: \n{{quote}}{comment_body}{{quote}}"
        return body

    def map_as_jira_description(self):
        """Extract description as comment from Webhook Event"""
        comment: BugzillaWebhookComment = self.bug.comment
        comment_body: str = comment.body
        body = f"*(description)*: \n{{quote}}{comment_body}{{quote}}"
        return body

    def map_as_tuple_of_field_dict_and_comments(
        self,
        status_log_enabled: bool = True,
        assignee_log_enabled: bool = True,
    ) -> Tuple[Dict, List[str]]:
        """Extract update dict and comment list from Webhook Event"""

        comments: List = []
        bug: BugzillaBug = self.bug  # type: ignore

        update_fields: dict = {
            "summary": bug.summary,
            "labels": bug.get_jira_labels(),
        }
        if self.event.changes:
            user = self.event.user.login if self.event.user else "unknown"
            for change in self.event.changes:

                if status_log_enabled and change.field in ["status", "resolution"]:
                    comments.append(
                        {
                            "modified by": user,
                            "resolution": bug.resolution,
                            "status": bug.status,
                        }
                    )

                if assignee_log_enabled and change.field in ["assigned_to", "assignee"]:
                    comments.append({"assignee": bug.assigned_to})

                if change.field == "reporter":
                    update_fields[change.field] = change.added

        comments_as_str: List[str] = [json.dumps(comment) for comment in comments]
        return update_fields, comments_as_str


class BugzillaApiResponse(BaseModel):
    """Bugzilla Response Object"""

    faults: Optional[List]
    bugs: Optional[List[BugzillaBug]]
