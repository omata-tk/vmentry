from flask import redirect, render_template, request, session, url_for

from core import db
from services import redmine
from web.state import (
    SESSION_API_KEY,
    SESSION_IS_ADMIN,
    SESSION_USER_NAME,
    SESSION_USER_ROLE,
)


def get_session_api_key():
    return (session.get(SESSION_API_KEY) or "").strip()


def get_session_user_name():
    return (session.get(SESSION_USER_NAME) or "").strip() or "Unknown User"


def is_admin_session():
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
                session[SESSION_USER_NAME] = db.get_setting("admin_username", "")
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
        session.clear()
        return redirect(url_for("login"))
