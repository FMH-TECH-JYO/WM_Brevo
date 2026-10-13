"""Auth routes: login, logout."""
import logging
from flask import request, jsonify, session, redirect, url_for, render_template, current_app
from app.auth import auth_bp

log = logging.getLogger(__name__)


def _check_credentials(username: str, password: str) -> bool:
    expected_user = current_app.config.get("APP_USERNAME", "admin")
    expected_pass = current_app.config.get("APP_PASSWORD", "changeme")
    return username == expected_user and password == expected_pass


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        username = data.get("username", "")
        password = data.get("password", "")
        if _check_credentials(username, password):
            session["logged_in"] = True
            session["username"] = username
            log.info("User %r logged in", username)
            return jsonify({"success": True})
        log.warning("Failed login attempt for user %r", username)
        return jsonify({"success": False, "error": {"code": "AUTH_FAILED", "message": "Invalid credentials"}}), 401
    # GET: serve the main page (login is embedded in index.html)
    return render_template("index.html")


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"success": True})
