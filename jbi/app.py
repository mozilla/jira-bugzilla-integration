"""
Core FastAPI app (setup, middleware)
"""
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from secrets import token_hex
from typing import Any, Awaitable, Callable

import sentry_sdk
from asgi_correlation_id import CorrelationIdMiddleware
from dockerflow import checks
from dockerflow.version import get_version
from fastapi import FastAPI, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import jbi.jira
from jbi.configuration import ACTIONS
from jbi.environment import get_settings
from jbi.log import CONFIG, format_request_summary_fields
from jbi.router import router

SRC_DIR = Path(__file__).parent

settings = get_settings()
version_info = get_version()

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    jira_service = jbi.jira.get_service()
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

app.include_router(router)
app.mount("/static", StaticFiles(directory=SRC_DIR / "static"), name="static")


@app.middleware("http")
async def request_summary(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Middleware to log request info"""
    summary_logger = logging.getLogger("request.summary")
    request_time = time.time()
    try:
        response = await call_next(request)
        log_fields = format_request_summary_fields(
            request, request_time, status_code=response.status_code
        )
        summary_logger.info("", extra=log_fields)
        return response
    except Exception as exc:
        log_fields = format_request_summary_fields(
            request, request_time, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        summary_logger.info(exc, extra=log_fields)
        raise


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


app.add_middleware(
    CorrelationIdMiddleware,
    header_name="X-Request-Id",
    generator=lambda: token_hex(16),
    validator=None,
)
