"""
Conf file extracted from:
https://github.com/tiangolo/uvicorn-gunicorn-docker/blob/2daa3e3873c837d5781feb4ff6a40a89f791f81b/docker-images/gunicorn_conf.py
"""
# pylint: disable=invalid-name

import multiprocessing
import os
from typing import Optional

from prometheus_client import multiprocess
from pydantic import BaseSettings, root_validator, validator


class GunicornSettings(BaseSettings):
    keep_alive: int = 5
    timeout: int = 120
    graceful_timeout: int = 120
    log_level: str = "info"
    host: str = "0.0.0.0"
    port: str = "8000"
    bind: Optional[str]

    @validator("bind")
    def set_bind(cls, bind, values):
        return bind if bind else f"{values['host']}:{values['port']}"


workers_per_core_str = os.getenv("WORKERS_PER_CORE", "1")
max_workers_str = os.getenv("MAX_WORKERS")
use_max_workers = None
if max_workers_str:
    use_max_workers = int(max_workers_str)
web_concurrency_str = os.getenv("WEB_CONCURRENCY", None)

cores = multiprocessing.cpu_count()
workers_per_core = float(workers_per_core_str)
default_web_concurrency = workers_per_core * cores
if web_concurrency_str:
    web_concurrency = int(web_concurrency_str)
    assert web_concurrency > 0
else:
    web_concurrency = max(int(default_web_concurrency), 2)
    if use_max_workers:
        web_concurrency = min(web_concurrency, use_max_workers)
accesslog_var = os.getenv("ACCESS_LOG", "-")
use_accesslog = accesslog_var or None
errorlog_var = os.getenv("ERROR_LOG", "-")
use_errorlog = errorlog_var or None


gunicorn_settings = GunicornSettings()

# Gunicorn config variables
loglevel = gunicorn_settings.log_level
workers = web_concurrency
bind = gunicorn_settings.bind
errorlog = use_errorlog
worker_tmp_dir = "/dev/shm"
accesslog = use_accesslog
graceful_timeout = gunicorn_settings.graceful_timeout
timeout = gunicorn_settings.timeout
keepalive = gunicorn_settings.keep_alive


# For debugging and testing
log_data = {
    "loglevel": loglevel,
    "workers": workers,
    "bind": bind,
    "graceful_timeout": graceful_timeout,
    "timeout": timeout,
    "keepalive": keepalive,
    "errorlog": errorlog,
    "accesslog": accesslog,
    # Additional, non-gunicorn variables
    "workers_per_core": workers_per_core,
    "use_max_workers": use_max_workers,
    "host": gunicorn_settings.host,
    "port": gunicorn_settings.port,
}


def child_exit(server, worker):  # pylint: disable=missing-function-docstring
    multiprocess.mark_process_dead(worker.pid)
