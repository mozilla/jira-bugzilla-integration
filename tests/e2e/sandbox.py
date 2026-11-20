"""A thin client for the e2e scenarios: real Jira, real Bugzilla, real JBI.

The two producers are stood in for, because neither can be pointed at a
developer's machine: BMO webhooks (`post_bugzilla_event`) and the central
Jira Automation rule (`post_jira_event`, which builds the same body the rule
sends from the live issue). Everything in between -- JBI, its config, both
APIs -- is the real thing.

Every bug and issue a test creates is recorded and cleaned up afterwards:
bugs are resolved INVALID (Bugzilla cannot delete), issues are deleted.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import requests

UNIQUE = uuid.uuid4().hex[:8]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Sandbox:
    def __init__(self, env: dict[str, str]):
        self.env = env
        self.bmo = env["BUGZILLA_BASE_URL"].rstrip("/")
        self.jira = env["JIRA_BASE_URL"].rstrip("/")
        self.jbi = env["JBI_E2E_BASE_URL"].rstrip("/")
        self.tag = env["JBI_E2E_WHITEBOARD_TAG"]
        self.project = env["JBI_E2E_JIRA_PROJECT"]
        self.product = env["JBI_E2E_BUGZILLA_PRODUCT"]
        self.component = env["JBI_E2E_BUGZILLA_COMPONENT"]
        self._bmo = requests.Session()
        self._bmo.headers["X-BUGZILLA-API-KEY"] = env["BUGZILLA_API_KEY"]
        self._jira = requests.Session()
        self._jira.auth = (env["JIRA_USERNAME"], env["JIRA_API_KEY"])
        self._jbi = requests.Session()
        self._jbi.headers["X-Api-Key"] = env["JBI_API_KEY"]
        self.created_bugs: list[int] = []
        self.created_issues: list[str] = []
        self._me: Optional[dict] = None

    # --- Bugzilla -----------------------------------------------------------

    def bug(self, bug_id: int) -> dict:
        resp = self._bmo.get(f"{self.bmo}/rest/bug/{bug_id}", timeout=30)
        resp.raise_for_status()
        return resp.json()["bugs"][0]

    def comments(self, bug_id: int) -> list[str]:
        resp = self._bmo.get(f"{self.bmo}/rest/bug/{bug_id}/comment", timeout=30)
        resp.raise_for_status()
        return [c["text"] for c in resp.json()["bugs"][str(bug_id)]["comments"]]

    def file_bug(self, summary: str, tagged: bool = True, **fields: Any) -> int:
        self._counter = getattr(self, "_counter", 0) + 1
        token = f"e2e{UNIQUE}n{self._counter}"
        body = {
            "product": self.product,
            "component": self.component,
            "summary": f"{token} {summary}",
            "description": "JBI e2e scenario. Safe to close.",
            "version": "unspecified",
            "type": "defect",
            "op_sys": "Unspecified",
            "platform": "Unspecified",
            "severity": "S3",
            "priority": "P2",
            **({"whiteboard": f"[{self.tag}]"} if tagged else {}),
            **fields,
        }
        resp = self._bmo.post(f"{self.bmo}/rest/bug", json=body, timeout=60)
        resp.raise_for_status()
        bug_id = resp.json()["id"]
        self.created_bugs.append(bug_id)
        return bug_id

    def update_bug(self, bug_id: int, **fields: Any) -> None:
        resp = self._bmo.put(f"{self.bmo}/rest/bug/{bug_id}", json=fields, timeout=60)
        resp.raise_for_status()

    def linked_issue(self, bug_id: int) -> Optional[str]:
        for link in self.bug(bug_id).get("see_also") or []:
            if f"/browse/{self.project}-" in link:
                return link.rstrip("/").rsplit("/", 1)[-1]
        return None

    # --- Jira --------------------------------------------------------------

    def issue(self, key: str, fields: str = "*all", expand: str = "") -> dict:
        params = {"fields": fields, **({"expand": expand} if expand else {})}
        resp = self._jira.get(
            f"{self.jira}/rest/api/2/issue/{key}", params=params, timeout=30
        )
        resp.raise_for_status()
        return resp.json()

    def issue_exists(self, key: str) -> bool:
        resp = self._jira.get(
            f"{self.jira}/rest/api/2/issue/{key}", params={"fields": "key"}, timeout=30
        )
        return resp.status_code == 200

    def search(self, jql: str) -> list[str]:
        resp = self._jira.post(
            f"{self.jira}/rest/api/3/search/jql",
            json={"jql": jql, "fields": ["key"], "maxResults": 50},
            timeout=30,
        )
        resp.raise_for_status()
        return [i["key"] for i in resp.json().get("issues", [])]

    def issues_for_bug(self, bug_id: int) -> list[str]:
        """Every issue in the project whose summary carries the bug's token.

        Counts duplicates, which the see_also link alone cannot (Invariant A).
        Jira's search index lags writes by a few seconds, hence `eventually`.
        """
        token = (self.bug(bug_id)["summary"] or "").split(" ", 1)[0]
        return self.search(f'project = {self.project} AND summary ~ "{token}"')

    def me(self) -> dict:
        if self._me is None:
            resp = self._jira.get(f"{self.jira}/rest/api/2/myself", timeout=30)
            resp.raise_for_status()
            self._me = resp.json()
        return self._me

    def transition(
        self, key: str, to_status: str, resolution: Optional[str] = None
    ) -> None:
        resp = self._jira.get(
            f"{self.jira}/rest/api/2/issue/{key}/transitions", timeout=30
        )
        resp.raise_for_status()
        match = [
            t
            for t in resp.json()["transitions"]
            if t["to"]["name"].lower() == to_status.lower()
        ]
        assert match, f"no transition from {key} to {to_status!r}"
        body: dict[str, Any] = {"transition": {"id": match[0]["id"]}}
        if resolution:
            body["fields"] = {"resolution": {"name": resolution}}
        resp = self._jira.post(
            f"{self.jira}/rest/api/2/issue/{key}/transitions", json=body, timeout=30
        )
        if resp.status_code == 400 and resolution:
            # The resolution is not on this workflow's transition screen.
            body.pop("fields")
            resp = self._jira.post(
                f"{self.jira}/rest/api/2/issue/{key}/transitions", json=body, timeout=30
            )
        resp.raise_for_status()

    def add_jira_comment(self, key: str, body: str) -> dict:
        resp = self._jira.post(
            f"{self.jira}/rest/api/2/issue/{key}/comment",
            json={"body": body},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def label(self, key: str, add: str = "", remove: str = "") -> None:
        ops = ([{"add": add}] if add else []) + ([{"remove": remove}] if remove else [])
        resp = self._jira.put(
            f"{self.jira}/rest/api/2/issue/{key}",
            json={"update": {"labels": ops}},
            timeout=30,
        )
        resp.raise_for_status()

    def status_transitions(self, key: str) -> int:
        histories = self.issue(key, fields="status", expand="changelog")["changelog"][
            "histories"
        ]
        return sum(1 for h in histories for i in h["items"] if i["field"] == "status")

    # --- Driving JBI ---------------------------------------------------------

    def post_bugzilla_event(
        self,
        bug_id: int,
        action: str = "create",
        changed: tuple[str, ...] = (),
        attachment: Optional[dict] = None,
    ) -> dict:
        bug = self.bug(bug_id)
        target = "bug"
        if attachment is not None:
            bug["attachment"] = attachment
            target = "attachment"
        payload = {
            "webhook_id": 0,
            "webhook_name": "e2e",
            "event": {
                "action": action,
                "target": target,
                "routing_key": f"{target}.{action}",
                "time": now_iso(),
                "user": {"id": 0, "login": "e2e@localhost", "real_name": "e2e"},
                "changes": [
                    {"field": f, "removed": "", "added": str(bug.get(f, ""))}
                    for f in changed
                ]
                or None,
            },
            "bug": bug,
        }
        resp = self._jbi.post(f"{self.jbi}/bugzilla_webhook", json=payload, timeout=180)
        resp.raise_for_status()
        return resp.json()

    def post_jira_event(
        self,
        key: str,
        changes: list[tuple[str, str, str]] = (),  # type: ignore[assignment]
        comment: Optional[dict] = None,
    ) -> requests.Response:
        """Post what the central Automation rule would, built from the live issue."""
        f = self.issue(key)["fields"]
        # The change was made with JBI's own credentials (the sandbox has no
        # second account), but the event models what the Automation rule sends
        # for a *person's* change: a different initiator. With JBI's id here,
        # Invariant C would correctly ignore the event.
        human = {
            "accountId": self.env.get("JBI_E2E_ACTOR_ACCOUNT_ID") or "e2e-human",
            "displayName": "e2e human",
        }

        def named(value):
            return {"name": (value or {}).get("name", "")}

        assignee = f.get("assignee") or {}
        payload: dict[str, Any] = {
            "webhookEvent": "comment_created" if comment else "jira:issue_updated",
            "timestamp": int(time.time() * 1000),
            "user": human,
            "issue": {
                "id": self.issue(key, fields="key")["id"],
                "key": key,
                "fields": {
                    "summary": f.get("summary"),
                    "project": {"key": f["project"]["key"]},
                    "created": f.get("created"),
                    "reporter": {
                        "accountId": (f.get("reporter") or {}).get("accountId", "")
                    },
                    "status": {
                        "name": f["status"]["name"],
                        "statusCategory": {"key": f["status"]["statusCategory"]["key"]},
                    },
                    "resolution": named(f.get("resolution")),
                    "priority": named(f.get("priority")),
                    "assignee": {
                        "accountId": assignee.get("accountId", ""),
                        "displayName": assignee.get("displayName", ""),
                        "emailAddress": assignee.get("emailAddress", ""),
                    },
                    "security": named(f.get("security")),
                    "labels": f.get("labels") or [],
                },
            },
            "changelog": {
                "items": [{"field": "__rule__"}]
                + [
                    {"field": field, "fromString": old, "toString": new}
                    for field, old, new in changes
                ]
            },
        }
        if comment:
            payload["comment"] = {
                "id": comment["id"],
                "body": comment["body"],
                "internal": "false",
                "visibility": {"type": "", "value": ""},
                "author": {**human, "emailAddress": ""},
            }
            payload["changelog"] = {"items": [{"field": "comment"}]}
        return self._jbi.post(f"{self.jbi}/jira_webhook", json=payload, timeout=180)

    # --- Waiting and cleanup ---------------------------------------------------

    @staticmethod
    def eventually(
        check: Callable[[], Any], timeout: float = 60, every: float = 2
    ) -> Any:
        """Poll until `check()` is truthy. Synchronous JBI rarely needs it, a
        Pub/Sub-backed one does."""
        deadline = time.monotonic() + timeout
        while True:
            result = check()
            if result or time.monotonic() > deadline:
                return result
            time.sleep(every)

    def track_issue(self, key: Optional[str]) -> Optional[str]:
        if key and key not in self.created_issues:
            self.created_issues.append(key)
        return key

    def cleanup(self) -> list[str]:
        problems = []
        for key in reversed(self.created_issues):
            resp = self._jira.delete(
                f"{self.jira}/rest/api/2/issue/{key}",
                params={"deleteSubtasks": "true"},
                timeout=30,
            )
            if resp.status_code not in (204, 404):
                problems.append(f"{key}: HTTP {resp.status_code}")
        for bug_id in self.created_bugs:
            try:
                self.update_bug(
                    bug_id,
                    status="RESOLVED",
                    resolution="INVALID",
                    comment={"body": "JBI e2e scenario finished; closing."},
                )
            except requests.HTTPError as exc:
                problems.append(f"bug {bug_id}: {exc}")
        return problems
