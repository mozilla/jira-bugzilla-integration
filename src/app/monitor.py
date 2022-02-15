from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.app import environment

api_router = APIRouter(tags=["dockerflow"])


def check_bugzilla(settings):
    pass


def check_jira(settings):
    pass


def heartbeat(request: Request, settings: environment.Settings):
    """Return status of backing services, as required by Dockerflow."""
    data = {"jira": check_jira(settings), "bugzilla": check_bugzilla(settings)}
    status_code = 200
    if not data["jira"]["up"]:
        status_code = 503
    if not data["bugzilla"]["up"]:
        status_code = 503

    return JSONResponse(content=data, status_code=status_code)


@api_router.get("/__heartbeat__", tags=["Platform"])
def get_heartbeat(
    request: Request,
    settings: environment.Settings = Depends(environment.get_settings),
):
    return heartbeat(request, settings)


@api_router.head("/__heartbeat__", tags=["Platform"])
def head_heartbeat(
    request: Request,
    settings: environment.Settings = Depends(environment.get_settings),
):
    return heartbeat(request, settings)


def lbheartbeat(request: Request):
    """Return response when application is running, as required by Dockerflow."""
    return {"status": "OK"}


@api_router.get("/__lbheartbeat__", tags=["Platform"])
def get_lbheartbeat(request: Request):
    return lbheartbeat(request)


@api_router.head("/__lbheartbeat__", tags=["Platform"])
def head_lbheartbeat(request: Request):
    return lbheartbeat(request)


@api_router.get("/__version__", tags=["Platform"])
def version():
    """Return version.json, as required by Dockerflow."""
    return environment.get_version()
