import datetime
import logging
from typing import Any, Optional, TypedDict
from urllib.parse import ParseResult, urlparse

from pydantic import (
    AwareDatetime,
    BaseModel,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
    ValidatorFunctionWrapHandler,
)
from pydantic.functional_validators import WrapValidator
from typing_extensions import Annotated

logger = logging.getLogger(__name__)
JIRA_HOSTNAMES = ("jira", "atlassian")

BugId = TypedDict("BugId", {"id": Optional[int]})


def maybe_add_timezone(
    v: Any, handler: ValidatorFunctionWrapHandler, info: ValidationInfo
):
    if isinstance(v, str):
        try:
            return handler(v)
        except ValidationError:
            return handler(v + "+00:00")
    assert isinstance(v, datetime.datetime), "must be a datetime here"
    v = v.replace(tzinfo=datetime.timezone.utc)
    return v


SmartAwareDatetime = Annotated[AwareDatetime, WrapValidator(maybe_add_timezone)]


class WebhookUser(BaseModel, frozen=True):
    """Bugzilla User Object"""

    id: int
    login: str
    real_name: str


class WebhookEventChange(BaseModel, frozen=True, coerce_numbers_to_str=True):
    """Bugzilla Change Object"""

    field: str
    removed: str
    added: str


class WebhookEvent(BaseModel, frozen=True):
    """Bugzilla Event Object"""

    action: str
    time: SmartAwareDatetime
    user: Optional[WebhookUser] = None
    changes: Optional[list[WebhookEventChange]] = None
    target: Optional[str] = None
    routing_key: Optional[str] = None

    def changed_fields(self) -> list[str]:
        """Returns the names of changed fields in a bug"""

        return [c.field for c in self.changes] if self.changes else []


class WebhookComment(BaseModel, frozen=True):
    """Bugzilla Comment Object"""

    body: Optional[str] = None
    id: Optional[int] = None
    number: Optional[int] = None
    is_private: Optional[bool] = None
    creation_time: Optional[SmartAwareDatetime] = None


class AttachmentFlag(BaseModel, frozen=True):
    """Flag object associated with a Bugzilla attachment."""

    id: int
    name: str
    value: str


class WebhookAttachment(BaseModel, frozen=True):
    """Attachment object associated with a Bugzilla bug
    when the webhook event is of type "attachment".
    See https://bugzilla.mozilla.org/page.cgi?id=webhooks.html
    """

    id: int
    content_type: str
    creation_time: SmartAwareDatetime
    description: str
    file_name: str
    flags: list[AttachmentFlag]
    is_obsolete: bool
    is_patch: bool
    is_private: bool


class Bug(BaseModel, frozen=True):
    """Bugzilla Bug Object"""

    id: int
    is_private: Optional[bool] = None
    type: Optional[str] = None
    product: Optional[str] = None
    component: Optional[str] = None
    whiteboard: Optional[str] = None
    keywords: Optional[list] = None
    flags: Optional[list] = None
    groups: Optional[list] = None
    status: Optional[str] = None
    resolution: Optional[str] = None
    see_also: Optional[list] = None
    summary: Optional[str] = None
    severity: Optional[str] = None
    priority: Optional[str] = None
    creator: Optional[str] = None
    assigned_to: Optional[str] = None
    comment: Optional[WebhookComment] = None
    attachment: Optional[WebhookAttachment] = None
    # Custom field Firefox for story points
    cf_fx_points: Optional[str] = None

    @property
    def product_component(self) -> str:
        """Return the component prefixed with the product
        as show in the Bugzilla UI (eg. ``Core::General``).
        """
        result = self.product + "::" if self.product else ""
        return result + self.component if self.component else result

    def is_assigned(self) -> bool:
        """Return `true` if the bug is assigned to a user."""
        return self.assigned_to != "nobody@mozilla.org"

    def extract_from_see_also(self, project_key):
        """Extract Jira Issue Key from see_also if jira url present"""
        if not self.see_also or len(self.see_also) == 0:
            return None

        candidates = []
        for url in self.see_also:
            try:
                parsed_url: ParseResult = urlparse(url=url)
                host_parts = parsed_url.hostname.split(".")
            except (ValueError, AttributeError):
                logger.info(
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
                    # Issue keys are like `{project_key}-{number}`
                    if parsed_jira_key.startswith(f"{project_key}-"):
                        return parsed_jira_key
                    # If not obvious, then keep this link as candidate.
                    candidates.append(parsed_jira_key)

        return candidates[0] if candidates else None


class WebhookRequest(BaseModel, frozen=True):
    """Bugzilla Webhook Request Object"""

    webhook_id: int
    webhook_name: str
    event: WebhookEvent
    bug: Bug


class Comment(BaseModel, frozen=True):
    """Bugzilla Comment"""

    id: int
    text: str
    is_private: bool
    creator: str


BugzillaComments = TypeAdapter(list[Comment])


class ApiResponse(BaseModel, frozen=True):
    """Bugzilla Response Object"""

    faults: Optional[list] = None
    bugs: Optional[list[Bug]] = None


class Webhook(BaseModel, frozen=True):
    """Bugzilla Webhook"""

    id: int
    name: str
    url: str
    event: str
    product: str
    component: str
    enabled: bool
    errors: int
    # Ignored fields:
    # creator: str

    @property
    def slug(self):
        """Return readable identifier"""
        name = self.name.replace(" ", "-").lower()
        product = self.product.replace(" ", "-").lower()
        return f"{self.id}-{name}-{product}"


class WebhooksResponse(BaseModel, frozen=True):
    """Bugzilla Webhooks List Response Object"""

    webhooks: Optional[list[Webhook]] = None
