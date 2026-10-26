"""Models for inbound Jira events (the Jira -> BMO direction).

These describe the payload posted by the central Jira Automation rule
("Send web request" -> `POST /jira_webhook`, see the plan's section 12).

Everything is optional and unknown keys are ignored on purpose: the payload is
assembled by a rule we do not fully control, its shape varies by trigger, and
JBI must degrade to "ignore this event" rather than 422 when a field is
missing. The reverse steps decide what they can act on.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, model_validator

from jbi.bugzilla.models import SmartAwareDatetime


class LenientModel(BaseModel):
    """Base for inbound payloads: immutable, tolerant of extra keys and of
    numeric ids.

    Jira sends ids as JSON numbers (`"id": 747979`, `"id": 3`) while the
    REST documentation shows strings, and pydantic v2 does not coerce int to
    str by default -- so without `coerce_numbers_to_str` a real Automation
    payload fails validation on a field we do not even use.
    """

    model_config = ConfigDict(extra="ignore", frozen=True, coerce_numbers_to_str=True)

    @model_validator(mode="before")
    @classmethod
    def _empty_strings_are_absent(cls, data):
        """Treat `""` as a missing value.

        Jira Automation renders an unset smart value as an empty string, so a
        custom request body yields `{"name": ""}` where the built-in format
        sends `null`. Without this, an unrestricted issue arrives carrying a
        present-but-empty `security` object, which the Invariant D guard reads
        as "embargoed" and uses to block every free-text write-back -- and an
        unassigned issue arrives with an empty `accountId` that looks like a
        real user. Normalising here keeps that difference out of every caller.
        """
        if not isinstance(data, dict):
            return data
        return {
            key: (None if isinstance(value, str) and not value.strip() else value)
            for key, value in data.items()
        }


class JiraUser(LenientModel):
    """A Jira Cloud user.

    `accountId` is the only stable identifier: `emailAddress` can be hidden by
    privacy settings and `displayName` changes freely (see plan section 5).
    """

    accountId: Optional[str] = None
    displayName: Optional[str] = None
    emailAddress: Optional[str] = None


class JiraStatusCategory(LenientModel):
    """One of Jira's three built-in status categories.

    `key` is `new`, `indeterminate` or `done` in every project, regardless of
    the workflow's custom status names. The reverse status mapping keys off
    this rather than inverting the many-to-one `status_map` (plan section 4.1).
    """

    key: Optional[str] = None
    colorName: Optional[str] = None


class JiraStatus(LenientModel):
    """A Jira status and the category it belongs to."""

    name: Optional[str] = None
    statusCategory: Optional[JiraStatusCategory] = None


class JiraNamedValue(LenientModel):
    """A Jira field represented as `{"name": ...}` (resolution, priority...)."""

    name: Optional[str] = None


class JiraProject(LenientModel):
    """The project an issue belongs to."""

    key: Optional[str] = None


class JiraVisibility(LenientModel):
    """A role/group restriction on a Jira comment."""

    type: Optional[str] = None
    value: Optional[str] = None
    identifier: Optional[str] = None


class JiraSecurityLevel(LenientModel):
    """An issue security level -- Jira's embargo mechanism."""

    id: Optional[str] = None
    name: Optional[str] = None


class JiraIssueFields(LenientModel):
    """The subset of issue fields the reverse direction reads."""

    summary: Optional[str] = None
    status: Optional[JiraStatus] = None
    resolution: Optional[JiraNamedValue] = None
    priority: Optional[JiraNamedValue] = None
    assignee: Optional[JiraUser] = None
    project: Optional[JiraProject] = None
    # Set when the issue carries an issue security level (embargoed work).
    security: Optional[JiraSecurityLevel] = None
    # `None` means the payload did not carry labels at all, which is different
    # from "no labels" -- the stop-label check has to fetch them in that case
    # rather than assume the issue is unlabelled.
    labels: Optional[list[str]] = None


class JiraIssue(LenientModel):
    """The issue an inbound event is about."""

    id: Optional[str] = None
    key: Optional[str] = None
    fields: Optional[JiraIssueFields] = None

    @property
    def project_key(self) -> Optional[str]:
        """Return the issue's project key, from `fields` or the issue key."""
        if self.fields and self.fields.project and self.fields.project.key:
            return self.fields.project.key
        if self.key and "-" in self.key:
            return self.key.rsplit("-", 1)[0]
        return None

    @property
    def status_category(self) -> Optional[str]:
        """Return the issue's status category key (`new`/`indeterminate`/`done`)."""
        if self.fields and self.fields.status and self.fields.status.statusCategory:
            return self.fields.status.statusCategory.key
        return None


class JiraChangelogItem(LenientModel):
    """One field change within an inbound event."""

    field: Optional[str] = None
    fieldId: Optional[str] = None
    fromString: Optional[str] = None
    toString: Optional[str] = None


class JiraChangelog(LenientModel):
    """The set of field changes carried by an inbound event."""

    items: list[JiraChangelogItem] = []


class JiraComment(LenientModel):
    """A comment added on the Jira side."""

    id: Optional[str] = None
    body: Optional[str] = None
    author: Optional[JiraUser] = None
    created: Optional[SmartAwareDatetime] = None
    # Present when the comment is limited to a project role or group.
    visibility: Optional[JiraVisibility] = None
    # Two spellings of the same idea, and they are **inverses**:
    # `jsdPublic: false` means internal-only, while `internal: true` means
    # internal-only. Jira's own webhooks send the first; Automation's
    # `{{comment.internal}}` smart value produces the second. Modelling only
    # one of them invites exactly the confusion that blocked every public
    # comment on the first attempt here.
    jsdPublic: Optional[bool] = None
    internal: Optional[bool] = None


class JiraWebhookRequest(LenientModel):
    """The payload posted by the Jira Automation rule.

    `user` is the field Jira's own webhooks use; `actor` is what an Automation
    rule sends when it forwards `{{initiator}}`. Both are accepted so the same
    endpoint works with either producer.
    """

    webhookEvent: Optional[str] = None
    issue: Optional[JiraIssue] = None
    user: Optional[JiraUser] = None
    actor: Optional[JiraUser] = None
    changelog: Optional[JiraChangelog] = None
    comment: Optional[JiraComment] = None
    timestamp: Optional[int] = None

    @property
    def actor_account_id(self) -> Optional[str]:
        """Return the accountId of whoever caused this event, if known.

        Falls back to the comment author, because a `comment_created` payload
        may carry the author only.
        """
        for candidate in (self.user, self.actor):
            if candidate and candidate.accountId:
                return candidate.accountId
        if self.comment and self.comment.author and self.comment.author.accountId:
            return self.comment.author.accountId
        return None

    def changed_fields(self) -> list[str]:
        """Return the names of the fields changed by this event."""
        if not self.changelog:
            return []
        return [item.field for item in self.changelog.items if item.field]

    @property
    def has_changelog(self) -> bool:
        """Whether this payload says *which* fields changed.

        Jira Automation's built-in "Issue data (Jira format)" body does not
        include one: it nests an empty `issue.changelog` (`histories: null`)
        and sends no top-level `changelog`. Verified against a real delivery.
        Without it JBI knows the issue's current state but not what moved,
        and "sync only what changed" is what stops a Jira event overwriting a
        BMO field a human just edited.
        """
        return bool(self.changelog and self.changelog.items)
