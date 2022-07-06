"""
Core FastAPI app (setup, middleware)
"""
import logging
import time
from datetime import datetime
from typing import Dict, List, Optional

import sentry_sdk
import uvicorn  # type: ignore
from fastapi import Body, Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware

from src.app.environment import get_settings
from src.app.log import configure_logging
from src.app.monitor import api_router as monitor_router
from src.jbi import configuration
from src.jbi.bugzilla import BugzillaWebhookRequest
from src.jbi.router import IgnoreInvalidRequestError, execute_action
from src.jbi.services import get_jira

templates = Jinja2Templates(directory="src/templates")

settings = get_settings()

configure_logging()

app = FastAPI(
    title="Jira Bugzilla Integration (JBI)",
    description="JBI v2 Platform",
    version="2.0.1",
)

app.include_router(monitor_router)
app.mount("/static", StaticFiles(directory="src/static"), name="static")

sentry_sdk.init(  # pylint: disable=abstract-class-instantiated  # noqa: E0110
    dsn=settings.sentry_dsn
)
app.add_middleware(SentryAsgiMiddleware)


@app.get("/", include_in_schema=False)
def root(request: Request):
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


@app.middleware("http")
async def request_summary(request: Request, call_next):
    """Middleware to log request info"""
    summary_logger = logging.getLogger("request.summary")
    previous_time = time.time()

    infos = {
        "agent": request.headers.get("User-Agent"),
        "path": request.url.path,
        "method": request.method,
        "lang": request.headers.get("Accept-Language"),
        "querystring": dict(request.query_params),
        "errno": 0,
    }

    response = await call_next(request)

    current = time.time()
    duration = int((current - previous_time) * 1000.0)
    isotimestamp = datetime.fromtimestamp(current).isoformat()
    infos = {"time": isotimestamp, "code": response.status_code, "t": duration, **infos}

    summary_logger.info("", extra=infos)

    return response


@app.post("/bugzilla_webhook")
def bugzilla_webhook(
    request: BugzillaWebhookRequest = Body(..., embed=False),
    action_map: Dict = Depends(configuration.get_actions),
):
    """API endpoint that Bugzilla Webhook Events request"""
    try:
        result = execute_action(request, action_map, settings)
        return JSONResponse(content=result, status_code=200)
    except IgnoreInvalidRequestError as exception:
        return JSONResponse(content={"error": str(exception)}, status_code=200)


@app.get("/whiteboard_tags/")
def get_whiteboard_tag(
    whiteboard_tag: Optional[str] = None,
    action_map: Dict = Depends(configuration.get_actions),
):
    """API for viewing whiteboard_tags and associated data"""
    if whiteboard_tag:
        if existing := action_map.get(whiteboard_tag):
            action_map = {whiteboard_tag: existing}
    return {k: v.dict() for k, v in action_map.items()}


@app.get("/jira_projects/")
def get_jira_projects():
    """API for viewing projects that are currently accessible by API"""
    jira = get_jira()
    visible_projects: List[Dict] = jira.projects(included_archived=None)
    return [project["key"] for project in visible_projects]


@app.get("/powered_by_jbi", response_class=HTMLResponse)
def powered_by_jbi(
    request: Request,
    enabled: Optional[bool] = None,
    action_map: Dict = Depends(configuration.get_actions),
):
    """API for `Powered By` endpoint"""
    context = {
        "request": request,
        "title": "Powered by JBI",
        "num_configs": len(action_map),
        "data": {k: v.dict() for k, v in action_map.items()},
        "enable_query": enabled,
    }
    return templates.TemplateResponse("powered_by_template.html", context)


if __name__ == "__main__":
    uvicorn.run(
        "app:app", host=settings.host, port=settings.port, reload=settings.app_reload
    )
