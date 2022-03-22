"""
Bugzilla Typed Objects for ease of use throughout JBI
"""
import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import ParseResult, urlparse

from pydantic import BaseModel  # pylint: disable=no-name-in-module


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
    time: Optional[datetime.datetime] = None
    user: Optional[BugzillaWebhookUser] = None
    changes: Optional[List[BugzillaWebhookEventChange]] = None
    target: Optional[str] = None
    routing_key: Optional[str] = None


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
        if self.number in [0]:  # Currently 0th comment is the description
            return True
        return False

    def is_comment_generic(self) -> bool:
        """All comments after comment-0 are generic"""
        is_description = self.is_comment_description()
        return not is_description

    def is_private_comment(self) -> bool:
        """Helper function to determine if this comment private--not accessible or open"""
        if self.is_private is None:
            self.is_private = False
        return self.is_private


class BugzillaBug(BaseModel):
    """Bugzilla Bug Object"""

    id: int
    is_private: Optional[bool] = None
    type: Optional[str] = None
    product: Optional[str] = None
    component: Optional[str] = None
    whiteboard: Optional[str] = None
    keywords: Optional[List] = None
    flags: Optional[List] = None
    status: Optional[str] = None
    resolution: Optional[str] = None
    see_also: Optional[List] = None
    summary: Optional[str] = None
    severity: Optional[str] = None
    priority: Optional[str] = None
    creator: Optional[str] = None
    assigned_to: Optional[str] = None
    comment: Optional[BugzillaWebhookComment] = None

    def get_whiteboard_count(self):
        """Get count of whiteboard labels"""
        if self.whiteboard is not None:
            return self.whiteboard.count("[")
        return 0

    def get_whiteboard_as_list(self):
        """Convert string whiteboard into list, splitting on ']' and removing '['."""
        if self.whiteboard is not None:
            split_list = self.whiteboard.replace("[", "").split("]")
            return [x.strip() for x in split_list if x not in ["", " "]]
        return []

    def get_whiteboard_with_brackets_as_list(self):
        """Convert string whiteboard into list, splitting on ']' and removing '['; then re-adding."""
        wb_list = self.get_whiteboard_as_list()
        if wb_list is not None and len(wb_list) > 0:
            return [f"[{element}]" for element in wb_list]
        return []

    def get_jbi_labels(self):
        """bugzilla is a required label for SYNC project and is being used to prevent gh syncing"""
        return (
            ["bugzilla"]
            + self.get_whiteboard_as_list()
            + self.get_whiteboard_with_brackets_as_list()
        )

    def get_potential_whiteboard_config_list(self):
        """Get all possible whiteboard_tag configuration values"""
        converted_list: List = []
        for whiteboard in self.get_whiteboard_as_list():
            converted_tag = self.convert_wb_to_tag(whiteboard=whiteboard)
            if converted_tag not in [None, "", " "]:
                converted_list.append(converted_tag)

        if len(converted_list) == 0:
            return None
        return converted_list

    @staticmethod
    def convert_wb_to_tag(whiteboard):
        """Extract tag from whiteboard label"""
        _exists = whiteboard not in (" ", "")
        if not _exists:
            return None
        return whiteboard.split(sep="-", maxsplit=1)[0].lower()

    def get_jira_issue_dict(self, jira_project_key):
        """Extract bug info as jira issue dictionary"""
        type_map: dict = {"enhancement": "Task", "task": "Task", "defect": "Bug"}
        return {
            "summary": self.summary,
            "project": {"key": jira_project_key},
            "labels": self.get_jbi_labels(),
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
                # Try parsing all see_also fields; skip errors.
                continue
        return None


class BugzillaWebhookRequest(BaseModel):
    """Bugzilla Webhook Request Object"""

    webhook_id: int
    webhook_name: str
    event: BugzillaWebhookEvent
    bug: Optional[BugzillaBug] = None

    def __eq__(self, other):
        """Equality check"""
        return (
            self.__dict__ == other.__dict__
            and self.bug.__dict__ == other.bug.__dict__
            and self.event.__dict__ == other.event.__dict__
        )

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
    ) -> Tuple[Dict, List]:
        """Extract update dict and comment list from Webhook Event"""

        comments: List = []
        if not self.bug:
            return {}, []
        bug: BugzillaBug = self.bug
        update_fields: dict = {"labels": bug.get_jbi_labels()}
        if self.event.changes:
            for change in self.event.changes:

                if status_log_enabled and change.field in ["status", "resolution"]:
                    comment_dict = {
                        "modified by": "unknown",
                        "resolution": bug.resolution,
                        "status": bug.status,
                    }
                    if self.event.user:
                        comment_dict["modified by"] = self.event.user.login

                    comments.append(comment_dict)

                if assignee_log_enabled and change.field in ["assigned_to", "assignee"]:
                    comments.append({"assignee": bug.assigned_to})

                if change.field in ["short_desc", "summary"]:
                    update_fields["summary"] = change.added

                if change.field == "reporter":
                    update_fields[change.field] = change.added

        return update_fields, comments


class BugzillaApiResponse(BaseModel):
    """Bugzilla Response Object"""

    faults: Optional[List] = None
    bugs: Optional[List[BugzillaBug]] = None
