import uvicorn  # type: ignore
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from src.app import monitor

app = FastAPI(
    title="Jira Bugzilla Integration (JBI)",
    description="JBI v2 Platform",
    version="0.1.0",
)

app.include_router(monitor.router)


@app.get("/", include_in_schema=False)
def root(request: Request):
    """GET via root redirects to /docs.
    - Args:
    - Returns:
        - **redirect**: Redirects call to ./docs
    """
    return RedirectResponse(url="./docs")


@app.get("/bugzilla_webhook")
def bugzilla_webhook(request: Request):
    pass


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=80, reload=True)
