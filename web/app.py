import os
from datetime import timedelta

from flask import Flask
from flask_session import Session

from core import db
from web.admin_routes import register_admin_routes
from web.auth_routes import register_auth_routes
from web.entry_routes import register_entry_routes
from web.logging_utils import init_access_logging


def _str_to_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def create_app():
    app = Flask(__name__, template_folder="../templates")

    # 本番では必須。未設定なら起動を止める。
    secret = (os.getenv("FLASK_SECRET_KEY") or "").strip()
    if not secret:
        raise RuntimeError("FLASK_SECRET_KEY is required.")
    app.secret_key = secret

    session_type = (os.getenv("SESSION_TYPE") or "filesystem").strip().lower()
    session_dir = (os.getenv("SESSION_FILE_DIR") or "./data/flask_session").strip()
    idle_minutes = int((os.getenv("SESSION_IDLE_MINUTES") or "60").strip())

    app.config.update(
        SESSION_TYPE=session_type,
        SESSION_PERMANENT=True,
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=idle_minutes),
        SESSION_USE_SIGNER=True,
        SESSION_COOKIE_NAME="vm_entry_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=_str_to_bool(os.getenv("SESSION_COOKIE_SECURE"), default=True),
    )

    if session_type == "redis":
        import redis

        redis_url = (os.getenv("SESSION_REDIS_URL") or "redis://127.0.0.1:6379/0").strip()
        app.config["SESSION_REDIS"] = redis.from_url(redis_url)
    else:
        os.makedirs(session_dir, exist_ok=True)
        app.config["SESSION_FILE_DIR"] = session_dir

    Session(app)

    db.init_db()
    db.assert_runtime_db_ready()

    init_access_logging(app)
    register_auth_routes(app)
    register_admin_routes(app)
    register_entry_routes(app)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)