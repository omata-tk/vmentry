import logging
import time
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import g, request, session

from web.state import SESSION_USER_NAME

JST = ZoneInfo("Asia/Tokyo")
ACCESS_LOGGER_NAME = "vm_entry.access"


class JstIsoFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, JST)
        return dt.isoformat(timespec="seconds")


def _get_client_ip():
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return forwarded or (request.remote_addr or "-")


def _configure_access_logger(project_root):
    log_dir = Path(project_root) / "data" / "log"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_path = log_dir / "access.log"

    logger = logging.getLogger(ACCESS_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if logger.handlers:
        return logger

    handler = TimedRotatingFileHandler(
        filename=str(log_path),
        when="midnight",
        interval=1,
        backupCount=90,
        encoding="utf-8",
        delay=True,
        utc=False,
    )
    handler.suffix = "%Y-%m-%d"
    handler.setFormatter(JstIsoFormatter("%(asctime)s %(message)s"))
    logger.addHandler(handler)
    return logger


def init_access_logging(app):
    project_root = Path(app.root_path).parent
    access_logger = _configure_access_logger(project_root)

    @app.before_request
    def _access_log_start_timer():
        g.request_start = time.perf_counter()

    @app.after_request
    def _access_log_write(response):
        started = getattr(g, "request_start", None)
        elapsed_ms = int((time.perf_counter() - started) * 1000) if started is not None else 0

        user_name = session.get(SESSION_USER_NAME) or "-"
        user_agent = (request.headers.get("User-Agent") or "-").replace('"', r"\"")

        access_logger.info(
            "ip=%s method=%s path=%s status=%s ms=%s user=%s ua=\"%s\"",
            _get_client_ip(),
            request.method,
            request.full_path.rstrip("?"),
            response.status_code,
            elapsed_ms,
            user_name,
            user_agent,
        )
        return response
