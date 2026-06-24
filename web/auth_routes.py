import os

from flask import (
    has_request_context,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from core import db
from services import redmine
from web.state import (
    SESSION_API_KEY,
    SESSION_IS_ADMIN,
    SESSION_USER_NAME,
    SESSION_USER_ROLE,
)

def _env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _is_loopback_value(value):
    v = (value or "").strip().lower()
    return v in ("127.0.0.1", "::1", "localhost")


def is_local_debug_bypass():
    # 明示的に有効化した場合のみ動作
    if not _env_bool("LOCAL_DEBUG_BYPASS_AUTH", default=False):
        return False

    if not has_request_context():
        return False

    host = (request.host or "").split(":")[0].strip().lower()
    remote = (request.remote_addr or "").strip().lower()
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip().lower()

    # localhost 直アクセス、またはループバックIP からのアクセスのみ許可
    return _is_loopback_value(host) or _is_loopback_value(remote) or _is_loopback_value(forwarded)


def get_session_api_key():
    if is_local_debug_bypass():
        # あれば環境変数のAPIキーを使う。なければ空文字（画面確認用途）。
        return (os.getenv("REDMINE_API_KEY") or "").strip()
    return (session.get(SESSION_API_KEY) or "").strip()


def get_session_user_name():
    if is_local_debug_bypass():
        return (os.getenv("LOCAL_DEBUG_USER_NAME") or "Local Debug User").strip()
    return (session.get(SESSION_USER_NAME) or "").strip() or "Unknown User"


def is_admin_session():
    if is_local_debug_bypass():
        return True
    return bool(session.get(SESSION_IS_ADMIN))


def build_user_display_name(user):
    first_name = (user.get("firstname") or "").strip()
    last_name = (user.get("lastname") or "").strip()
    full_name = f"{last_name} {first_name}".strip()
    if full_name:
        return full_name
    return (user.get("login") or "").strip() or "Unknown User"


def register_auth_routes(app):
    @app.route("/login", methods=["GET", "POST"])
    def login():
         # localhostデバッグ時はログイン画面を経由しない
        if is_local_debug_bypass():
            return redirect(url_for("index"))
        
        if get_session_api_key() or is_admin_session():
            return redirect(url_for("index"))

        error = None
        if request.method == "POST":
            api_key = (request.form.get("api_key") or "").strip()
            if not api_key:
                error = "APIキーを入力してください。"
            elif db.is_admin_key(api_key) or (
                api_key and api_key == (db.get_setting("admin_username", "") or "").strip()
            ):
                session[SESSION_API_KEY] = ""
                session[SESSION_USER_NAME] = "管理者"
                session[SESSION_USER_ROLE] = "admin"
                session[SESSION_IS_ADMIN] = True
                return redirect(url_for("index"))
            else:
                try:
                    user = redmine.get_current_user(api_key=api_key)
                    session[SESSION_API_KEY] = api_key
                    session[SESSION_USER_NAME] = build_user_display_name(user)
                    session[SESSION_USER_ROLE] = "user"
                    session[SESSION_IS_ADMIN] = False
                    return redirect(url_for("index"))
                except Exception as exc:
                    error = str(exc)

        return render_template("login.html", error=error)

    @app.route("/logout", methods=["GET"])
    def logout():
        # localhostデバッグ時はセッションクリアをスキップ
        if is_local_debug_bypass():
            return redirect(url_for("index"))

        session.clear()
        return redirect(url_for("login"))
