"""
Core FastAPI app (setup, middleware)
"""

import secrets
from pathlib import Path
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse
from fastapi.security import APIKeyHeader, HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from jbi import jira
from jbi.bugzilla import models as bugzilla_models
from jbi.bugzilla import service as bugzilla_service
from jbi.configuration import get_actions
from jbi.environment import Settings, get_settings
from jbi.models import Actions
from jbi.queue import DeadLetterQueue, get_dl_queue
from jbi.runner import execute_or_queue

SettingsDep = Annotated[Settings, Depends(get_settings)]
ActionsDep = Annotated[Actions, Depends(get_actions)]
BugzillaServiceDep = Annotated[
    bugzilla_service.BugzillaService, Depends(bugzilla_service.get_service)
]
JiraServiceDep = Annotated[jira.JiraService, Depends(jira.get_service)]

router = APIRouter()


@router.get("/", include_in_schema=False)
def root(request: Request, settings: SettingsDep):
    """Expose key configuration"""
    return {
        "title": request.app.title,
        "description": request.app.description,
        "version": request.app.version,
        "documentation": request.app.docs_url,
        "configuration": {
            "jira_base_url": settings.jira_base_url,
            "bugzilla_base_url": settings.bugzilla_base_url,
        },
    }


header_scheme = APIKeyHeader(name="X-Api-Key", auto_error=False)
basicauth_scheme = HTTPBasic(auto_error=False)


def api_key_auth(
    settings: SettingsDep,
    api_key: Annotated[str, Depends(header_scheme)],
    basic_auth: Annotated[HTTPBasicCredentials, Depends(basicauth_scheme)],
):
    if not api_key and basic_auth:
        api_key = basic_auth.password
    if not api_key or not secrets.compare_digest(api_key, settings.jbi_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect API Key",
            headers={"WWW-Authenticate": "Basic"},
        )


@router.post(
    "/bugzilla_webhook",
    dependencies=[Depends(api_key_auth)],
)
async def bugzilla_webhook(
    request: Request,
    actions: ActionsDep,
    queue: Annotated[DeadLetterQueue, Depends(get_dl_queue)],
    webhook_request: bugzilla_models.WebhookRequest = Body(..., embed=False),
):
    """API endpoint that Bugzilla Webhook Events request"""
    return await execute_or_queue(webhook_request, queue, actions)


@router.get(
    "/dl_queue/",
    dependencies=[Depends(api_key_auth)],
)
async def inspect_dl_queue(queue: Annotated[DeadLetterQueue, Depends(get_dl_queue)]):
    """API for viewing queue content"""
    bugs = await queue.retrieve()
    results = []
    fields: dict[str, Any] = {
        "identifier": True,
        "rid": True,
        "error": True,
        "version": True,
        "payload": {
            "bug": {"id", "whiteboard", "product", "component"},
            "event": {"action", "time"},
        },
    }
    for items in bugs.values():
        async for item in items:
            results.append(item.model_dump(include=fields))
    return results


@router.delete("/dl_queue/{item_id}", dependencies=[Depends(api_key_auth)])
async def delete_queue_item_by_id(
    item_id: str, queue: Annotated[DeadLetterQueue, Depends(get_dl_queue)]
):
    item_exists = await queue.exists(item_id)
    if item_exists:
        await queue.delete(item_id)
    else:
        raise HTTPException(
            status_code=404, detail=f"Item {item_id} not found in queue"
        )


@router.get(
    "/whiteboard_tags/",
    dependencies=[Depends(api_key_auth)],
)
def get_whiteboard_tags(
    actions: ActionsDep,
    whiteboard_tag: Optional[str] = None,
):
    """API for viewing whiteboard_tags and associated data"""
    if existing := actions.get(whiteboard_tag):
        return {whiteboard_tag: existing}
    return actions.by_tag


@router.get(
    "/bugzilla_webhooks/",
    dependencies=[Depends(api_key_auth)],
)
def get_bugzilla_webhooks(bugzilla_service: BugzillaServiceDep):
    """API for viewing webhooks details"""
    return bugzilla_service.list_webhooks()


@router.get(
    "/jira_projects/",
    dependencies=[Depends(api_key_auth)],
)
def get_jira_projects(jira_service: JiraServiceDep):
    """API for viewing projects that are currently accessible by API"""
    return jira_service.fetch_visible_projects()


SRC_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=SRC_DIR / "templates")


@router.get(
    "/powered_by_jbi/",
    dependencies=[Depends(api_key_auth)],
    response_class=HTMLResponse,
)
def powered_by_jbi(
    request: Request,
    actions: ActionsDep,
    enabled: Optional[bool] = None,
):
    """API for `Powered By` endpoint"""
    context = {
        "request": request,
        "title": "Powered by JBI",
        "actions": jsonable_encoder(actions),
        "enable_query": enabled,
    }
    return templates.TemplateResponse("powered_by_template.html", context)
