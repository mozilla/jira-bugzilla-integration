"""
Core FastAPI app (setup, middleware)
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

import sentry_sdk
from dockerflow import checks
from dockerflow.fastapi import router as dockerflow_router
from dockerflow.fastapi.middleware import (
    MozlogRequestSummaryLogger,
    RequestIdMiddleware,
)
from dockerflow.version import get_version
from fastapi import FastAPI, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import jbi.jira
from jbi.configuration import ACTIONS
from jbi.environment import get_settings
from jbi.log import CONFIG
from jbi.router import router

SRC_DIR = Path(__file__).parent
APP_DIR = Path(__file__).parents[1]

settings = get_settings()
version_info = get_version(APP_DIR)

logging.config.dictConfig(CONFIG)

logger = logging.getLogger(__name__)


def traces_sampler(sampling_context: dict[str, Any]) -> float:
    """Function to dynamically set Sentry sampling rates"""

    request_path = sampling_context.get("asgi_scope", {}).get("path")
    if request_path == "/__lbheartbeat__":
        # Drop all __lbheartbeat__ requests
        return 0
    return settings.sentry_traces_sample_rate


sentry_sdk.init(
    dsn=str(settings.sentry_dsn) if settings.sentry_dsn else None,
    traces_sampler=traces_sampler,
    release=version_info["version"],
)


# https://github.com/tiangolo/fastapi/discussions/9241
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    jira_service = jbi.jira.get_service()
    bugzilla_service = jbi.bugzilla.get_service()

    checks.register(bugzilla_service.check_bugzilla_connection, name="bugzilla.up")
    checks.register(
        bugzilla_service.check_bugzilla_webhooks,
        name="bugzilla.all_webhooks_enabled",
    )

    checks.register(jira_service.check_jira_connection, name="jira.up")
    checks.register_partial(
        jira_service.check_jira_all_projects_are_visible,
        ACTIONS,
        name="jira.all_projects_are_visible",
    )
    checks.register_partial(
        jira_service.check_jira_all_projects_have_permissions,
        ACTIONS,
        name="jira.all_projects_have_permissions",
    )
    checks.register_partial(
        jira_service.check_jira_all_project_custom_components_exist,
        ACTIONS,
        name="jira.all_project_custom_components_exist",
    )
    checks.register_partial(
        jira_service.check_jira_all_project_issue_types_exist,
        ACTIONS,
        name="jira.all_project_issue_types_exist",
    )
    checks.register(jira_service.check_jira_pandoc_install, name="jira.pandoc_install")

    yield


app = FastAPI(
    title="Jira Bugzilla Integration (JBI)",
    description="Platform providing synchronization of Bugzilla bugs to Jira issues.",
    version=version_info["version"],
    debug=settings.app_debug,
    lifespan=lifespan,
)

app.state.APP_DIR = APP_DIR
app.state.DOCKERFLOW_HEARTBEAT_FAILED_STATUS_CODE = 503
app.state.DOCKERFLOW_SUMMARY_LOG_QUERYSTRING = True

app.include_router(router)
app.include_router(dockerflow_router)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(MozlogRequestSummaryLogger)

app.mount("/static", StaticFiles(directory=SRC_DIR / "static"), name="static")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> Response:
    """
    Override the default exception handler for validation
    errors in order to log some information about malformed
    requests.
    """
    logger.error(
        "invalid incoming request: %s",
        exc,
        extra={
            "errors": exc.errors(),
        },
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": jsonable_encoder(exc.errors())},
    )
