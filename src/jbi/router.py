"""
Router dedicated to Jira Bugzilla Integration APIs
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.app import environment
from src.jbi import configuration

templates = Jinja2Templates(directory="src/templates")

api_router = APIRouter(tags=["JBI"])
api_router.mount("/static", StaticFiles(directory="src/static"), name="static")

jbi_logger = logging.getLogger("src.jbi")


def execute_request(request, settings):
    # Is request valid?
    # Is whiteboard known?
    # Execute desired action -- based on whiteboard config
    pass


@api_router.post("/bugzilla_webhook")
def bugzilla_webhook(
    request: Request,
    settings: environment.Settings = Depends(environment.get_settings),
):
    return execute_request(request, settings)


@api_router.get("/whiteboard_tags/")
def get_whiteboard_tag(
    whiteboard_tag: Optional[str] = None,
):
    data = configuration.get_yaml_configurations()
    if whiteboard_tag:
        wb_val = data.get(whiteboard_tag)
        if wb_val:
            data = wb_val
    return data


@api_router.get("/actions/")
def get_actions_by_type(action_type: Optional[str] = None):
    configured_actions = configuration.get_yaml_configurations()
    if action_type:
        data = [
            a["action"]
            for a in configured_actions.values()
            if a["action"].endswith(action_type)
        ]
    else:
        data = [a["action"] for a in configured_actions.values()]
    return data


@api_router.get("/powered_by_jbi", response_class=HTMLResponse)
def powered_by_jbi(request: Request, enabled: Optional[bool] = None):
    data = configuration.get_yaml_configurations()
    context = {
        "request": request,
        "title": "Powered by JBI",
        "num_configs": len(data),
        "data": data,
        "enable_query": enabled,
    }
    return templates.TemplateResponse("powered_by_template.html", context)
