import uvicorn

from jbi.environment import get_settings

settings = get_settings()


if __name__ == "__main__":
    server = uvicorn.Server(
        uvicorn.Config(
            "jbi.app:app",
            host=settings.host,
            port=settings.port,
            reload=settings.app_reload,
            log_config=None,
        )
    )
    server.run()
