import uvicorn  # type: ignore
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from src.app.environment import get_settings
from src.app.monitor import api_router as monitor_router
from src.jbi.router import api_router as jbi_router

app = FastAPI(
    title="Jira Bugzilla Integration (JBI)",
    description="JBI v2 Platform",
    version="0.1.0",
)

app.include_router(monitor_router)
app.include_router(jbi_router)


@app.get("/", include_in_schema=False)
def root(request: Request):
    """GET via root redirects to /docs.
    - Args:
    - Returns:
        - **redirect**: Redirects call to ./docs
    """
    return RedirectResponse(url="./docs")


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "app:app", host="0.0.0.0", port=settings.port, reload=settings.app_reload
    )
