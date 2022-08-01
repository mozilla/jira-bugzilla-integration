"""
Core FastAPI app (setup, middleware)
"""
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import sentry_sdk
from fastapi import Body, Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware

from src.app import configuration
from src.app.environment import get_settings
from src.app.monitor import api_router as monitor_router
from src.jbi.bugzilla import BugzillaWebhookRequest
from src.jbi.models import Actions
from src.jbi.runner import IgnoreInvalidRequestError, execute_action
from src.jbi.services import jira_visible_projects

SRC_DIR = Path(__file__).parents[1]

templates = Jinja2Templates(directory=SRC_DIR / "templates")

settings = get_settings()

app = FastAPI(
    title="Jira Bugzilla Integration (JBI)",
    description="JBI v2 Platform",
    version="3.1.0",
    debug=settings.app_debug,
)

app.include_router(monitor_router)
app.mount("/static", StaticFiles(directory=SRC_DIR / "static"), name="static")

sentry_sdk.init(  # pylint: disable=abstract-class-instantiated  # noqa: E0110
    dsn=settings.sentry_dsn
)
app.add_middleware(SentryAsgiMiddleware)


def format_log_fields(request: Request, request_time: float, status_code: int) -> Dict:
    """Prepare Fields for Mozlog request summary"""

    current_time = time.time()
    fields = {
        "agent": request.headers.get("User-Agent"),
        "path": request.url.path,
        "method": request.method,
        "lang": request.headers.get("Accept-Language"),
        "querystring": dict(request.query_params),
        "errno": 0,
        "t": int((current_time - request_time) * 1000.0),
        "time": datetime.fromtimestamp(current_time).isoformat(),
        "status_code": status_code,
    }
    return fields


@app.middleware("http")
async def request_summary(request: Request, call_next):
    """Middleware to log request info"""
    summary_logger = logging.getLogger("request.summary")
    request_time = time.time()
    try:
        response = await call_next(request)
        log_fields = format_log_fields(
            request, request_time, status_code=response.status_code
        )
        summary_logger.info("", extra=log_fields)
        return response
    except Exception as exc:
        log_fields = format_log_fields(request, request_time, status_code=500)
        summary_logger.info(exc, extra=log_fields)
        raise


@app.get("/", include_in_schema=False)
def root():
    """Expose key configuration"""
    return {
        "title": app.title,
        "description": app.description,
        "version": app.version,
        "documentation": app.docs_url,
        "configuration": {
            "jira_base_url": settings.jira_base_url,
            "bugzilla_base_url": settings.bugzilla_base_url,
        },
    }


@app.post("/bugzilla_webhook")
def bugzilla_webhook(
    request: BugzillaWebhookRequest = Body(..., embed=False),
    actions: Actions = Depends(configuration.get_actions),
):
    """API endpoint that Bugzilla Webhook Events request"""
    try:
        result = execute_action(request, actions, settings)
        return result
    except IgnoreInvalidRequestError as exception:
        return {"error": str(exception)}


@app.get("/whiteboard_tags/")
def get_whiteboard_tags(
    whiteboard_tag: Optional[str] = None,
    actions: Actions = Depends(configuration.get_actions),
):
    """API for viewing whiteboard_tags and associated data"""
    if existing := actions.get(whiteboard_tag):
        return {whiteboard_tag: existing}
    return actions.by_tag


@app.get("/jira_projects/")
def get_jira_projects():
    """API for viewing projects that are currently accessible by API"""
    visible_projects: List[Dict] = jira_visible_projects()
    return [project["key"] for project in visible_projects]


@app.get("/powered_by_jbi/", response_class=HTMLResponse)
def powered_by_jbi(
    request: Request,
    enabled: Optional[bool] = None,
    actions: Actions = Depends(configuration.get_actions),
):
    """API for `Powered By` endpoint"""
    context = {
        "request": request,
        "title": "Powered by JBI",
        "actions": jsonable_encoder(actions),
        "enable_query": enabled,
    }
    return templates.TemplateResponse("powered_by_template.html", context)
