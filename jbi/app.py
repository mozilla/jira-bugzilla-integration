"""
Core FastAPI app (setup, middleware)
"""
import logging
import time
from pathlib import Path
from typing import Awaitable, Callable

import sentry_sdk
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from jbi.environment import get_settings, get_version
from jbi.log import format_request_summary_fields
from jbi.router import router

SRC_DIR = Path(__file__).parent

settings = get_settings()
version_info = get_version()

sentry_sdk.init(
    dsn=settings.sentry_dsn,
    traces_sample_rate=settings.sentry_traces_sample_rate,
)


app = FastAPI(
    title="Jira Bugzilla Integration (JBI)",
    description="Platform providing default and customized synchronization for bugzilla bugs.",
    version=version_info["version"],
    debug=settings.app_debug,
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
            request, request_time, status_code=500
        )
        summary_logger.info(exc, extra=log_fields)
        raise
