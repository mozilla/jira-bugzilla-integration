"""
Bugzilla Typed Objects for ease of use throughout JBI
View additional bugzilla webhook documentation here: https://bugzilla.mozilla.org/page.cgi?id=webhooks.html

"""
import datetime
import json
import logging
from typing import Dict, List, Optional, Tuple
from urllib.parse import ParseResult, urlparse

from pydantic import BaseModel  # pylint: disable=no-name-in-module

from src.jbi.errors import ActionNotFoundError
from src.jbi.models import Action, Actions

logger = logging.getLogger(__name__)

JIRA_HOSTNAMES = ("jira", "atlassian")


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

    def changed_fields(self) -> Optional[List[str]]:
        """Returns the names of changed fields in a bug"""
        if self.changes:
            return [c.field for c in self.changes]

        # Private bugs don't include the changes field in the event, but the
        # field names are in the routing key.
        if self.routing_key is not None and self.routing_key[0:11] == "bug.modify:":
            return self.routing_key[11:].split(",")

        return None


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
    groups: Optional[List]
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

    def issue_type(self) -> str:
        """Get the Jira issue type for this bug"""
        type_map: dict = {"enhancement": "Task", "task": "Task", "defect": "Bug"}
        return type_map.get(self.type, "Task")

    def map_as_jira_issue(self) -> Dict:
        """Extract bug info as jira issue dictionary"""
        return {
            "summary": self.summary,
            "labels": self.get_jira_labels(),
        }

    def extract_from_see_also(self):
        """Extract Jira Issue Key from see_also if jira url present"""
        if not self.see_also and len(self.see_also) > 0:
            return None

        for url in self.see_also:  # pylint: disable=not-an-iterable
            try:
                parsed_url: ParseResult = urlparse(url=url)
                host_parts = parsed_url.hostname.split(".")
            except (ValueError, AttributeError):
                logger.debug(
                    "Bug %s `see_also` is not a URL: %s",
                    self.id,
                    url,
                    extra={
                        "bug": {
                            "id": self.id,
                        }
                    },
                )
                continue

            if any(part in JIRA_HOSTNAMES for part in host_parts):
                parsed_jira_key = parsed_url.path.rstrip("/").split("/")[-1]
                if parsed_jira_key:  # URL ending with /
                    return parsed_jira_key

        return None

    def lookup_action(self, actions: Actions) -> Tuple[str, Action]:
        """Find first matching action from bug's whiteboard list"""
        tags: List[str] = self.get_potential_whiteboard_config_list()
        for tag in tags:
            tag = tag.lower()
            if action := actions.get(tag):
                return tag, action
        raise ActionNotFoundError(", ".join(tags))


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

    def map_as_comments(
        self,
        status_log_enabled: bool = True,
        assignee_log_enabled: bool = True,
    ) -> List[str]:
        """Extract update dict and comment list from Webhook Event"""

        comments: List = []
        bug: BugzillaBug = self.bug  # type: ignore

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

        return [json.dumps(comment, indent=4) for comment in comments]


class BugzillaApiResponse(BaseModel):
    """Bugzilla Response Object"""

    faults: Optional[List]
    bugs: Optional[List[BugzillaBug]]
