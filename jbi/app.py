"""
Core FastAPI app (setup, middleware)
"""
import logging
import time
from pathlib import Path

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

from jbi.environment import get_settings
from jbi.log import format_request_summary_fields
from jbi.router import router

SRC_DIR = Path(__file__).parent

settings = get_settings()

sentry_sdk.init(
    dsn=settings.sentry_dsn,
    integrations=[
        StarletteIntegration(),
        FastApiIntegration(),
    ],
    # Set traces_sample_rate to 1.0 to capture 100% of transactions for performance monitoring.
    # TODO: Sentry recommends adjusting this value in production # pylint: disable=fixme
    traces_sample_rate=1.0,
)


app = FastAPI(
    title="Jira Bugzilla Integration (JBI)",
    description="JBI v2 Platform",
    version="3.2.2",
    debug=settings.app_debug,
)

app.include_router(router)
app.mount("/static", StaticFiles(directory=SRC_DIR / "static"), name="static")


@app.middleware("http")
async def request_summary(request: Request, call_next):
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
