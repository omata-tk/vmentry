import os

from flask import Flask

from core import db
from web.admin_routes import register_admin_routes
from web.auth_routes import register_auth_routes
from web.entry_routes import register_entry_routes
from web.logging_utils import init_access_logging


def create_app():
    app = Flask(__name__, template_folder="../templates")
    app.secret_key = os.getenv("FLASK_SECRET_KEY", "ticket-maker-dev-secret-change-me")

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
