"""Application factory: builds the Flask app, checks configuration and models, registers blueprints."""

import logging
import os
import secrets
from pathlib import Path

from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    send_from_directory,
    session,
    redirect,
    url_for,
)

from flask_wtf.csrf import CSRFError
from sqlalchemy import event
from sqlalchemy.engine import Engine

from config import get_config

from .constants import GRADE_NAMES, ROLES, UNIT_LABELS
from .extensions import csrf, db, limiter, login_manager
from .formatting import format_inr, format_ist, format_number, safe_http_url
from .security import apply_security_headers
from .services.costing import CostEstimator
from .services.ml_service import ModelService

log = logging.getLogger("civicpulse")


@event.listens_for(Engine, "connect")
def _sqlite_foreign_keys(dbapi_connection, connection_record):
    """SQLite ignores foreign keys unless this is switched on for every connection."""
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def debug_requested():
    """True if the operator asked for Flask debug mode."""
    return os.environ.get("FLASK_DEBUG", "").strip().lower() in (
        "1", "true", "yes", "on"
    )


def _check_configuration(app):
    env = app.config["ENV_NAME"]
    key = app.config.get("SECRET_KEY") or ""

    if key.startswith("replace-with"):
        raise RuntimeError(
            "SECRET_KEY still has the placeholder from .env.example. Generate a real key."
        )

    if env == "production":
        if len(key) < 32:
            raise RuntimeError(
                "SECRET_KEY must be set (at least 32 characters) in production."
            )

        if not app.config.get("ARTIFACT_MANIFEST_SHA256"):
            raise RuntimeError(
                "ARTIFACT_MANIFEST_SHA256 must be set in production to pin the model files"
            )

        if app.debug or debug_requested():
            raise RuntimeError(
                "Debug mode must be off in production: remove FLASK_DEBUG / --debug"
            )

        app.config["SESSION_COOKIE_SECURE"] = True

    elif not key:
        app.config["SECRET_KEY"] = secrets.token_hex(32)
        log.warning(
            "SECRET_KEY not set: using a random key for this %s run",
            env
        )


def create_app(config_name=None, overrides=None):

    app = Flask(
        __name__,
        instance_path=str(
            Path(__file__).resolve().parent.parent / "instance"
        )
    )

    app.config.from_object(get_config(config_name))

    if overrides:
        app.config.update(overrides)

    # --------------------------------------------------
    # PROTOTYPE MODE
    # --------------------------------------------------

    app.config["PROTOTYPE_MODE"] = True
    app.config["PROTOTYPE_ADMIN_USERNAME"] = "admin"

    # --------------------------------------------------
    # APPLICATION CONFIGURATION
    # --------------------------------------------------

    app.debug = (
        app.config["ENV_NAME"] == "development"
        and (
            debug_requested()
            or bool(app.config.get("DEBUG"))
        )
    )

    _check_configuration(app)

    Path(app.instance_path).mkdir(exist_ok=True)

    # --------------------------------------------------
    # EXTENSIONS
    # --------------------------------------------------

    db.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    login_manager.init_app(app)

    login_manager.login_view = "auth.login"
    login_manager.login_message = None
    login_manager.session_protection = None

    # --------------------------------------------------
    # MACHINE LEARNING INITIALIZATION
    # --------------------------------------------------

    ml = ModelService(
        app.config["ARTIFACT_DIR"],
        app.config.get("ARTIFACT_MANIFEST_SHA256", "")
    )

    app.extensions["civicpulse_ml"] = ml

    app.extensions["civicpulse_estimator"] = CostEstimator(
        ml,
        app.config["CONTINGENCY_PERCENT"],
        app.config["PRICE_INDEX_FACTOR"],
        app.config["PRICE_INDEX_SOURCE"]
    )

    if ml.version_notes:
        log.warning(
            "Library versions differ from training (golden samples still match): %s",
            ml.version_notes
        )

    # --------------------------------------------------
    # PROTOTYPE ADMINISTRATOR
    # --------------------------------------------------

    from .models import User

    @login_manager.user_loader
    def load_user(session_id):

        try:
            user_id, version = (
                int(part) for part in session_id.split(":")
            )

        except (ValueError, AttributeError):
            return None

        user = db.session.get(User, user_id)

        if (
            user is None
            or user.session_version != version
            or not user.is_active
        ):
            return None

        return user

    @login_manager.request_loader
    def load_prototype_user(request):

        if not app.config.get("PROTOTYPE_MODE"):
            return None

        return User.query.filter_by(
            username=app.config["PROTOTYPE_ADMIN_USERNAME"],
            active=True
        ).first()

    @login_manager.unauthorized_handler
    def unauthorized():

        if "/api/" in request.path:
            return jsonify(
                error="authentication required"
            ), 401

        if app.config.get("PROTOTYPE_MODE"):
            return redirect(url_for("dashboard.index"))

        from flask import flash

        flash("Please log in to continue.")

        return redirect(
            url_for(
                "auth.login",
                next=request.full_path if request.method == "GET" else None
            )
        )

    # --------------------------------------------------
    # REGISTER BLUEPRINTS
    # --------------------------------------------------

    from .blueprints import register_blueprints

    register_blueprints(app)

    # --------------------------------------------------
    # TEMPLATE FILTERS
    # --------------------------------------------------

    app.jinja_env.filters["inr"] = format_inr

    app.jinja_env.filters["inr_paise"] = (
        lambda v: format_inr(v, paise=True)
    )

    app.jinja_env.filters["grade_name"] = (
        lambda g: GRADE_NAMES.get(g, g)
    )

    app.jinja_env.filters["unit_label"] = (
        lambda u: UNIT_LABELS.get(u, u)
    )

    app.jinja_env.filters["num"] = format_number
    app.jinja_env.filters["ist"] = format_ist
    app.jinja_env.filters["http_url"] = safe_http_url

    app.jinja_env.globals["ROLES"] = ROLES
    app.jinja_env.globals["format_inr"] = format_inr

    # Expose prototype mode to templates.
    @app.context_processor
    def inject_prototype_mode():
        return {
            "prototype_mode": app.config["PROTOTYPE_MODE"]
        }

    # --------------------------------------------------
    # REQUEST HANDLING
    # --------------------------------------------------

    @app.before_request
    def _prototype_access():

        if not app.config.get("PROTOTYPE_MODE"):
            return None

        # Login and logout are unnecessary in prototype mode.
        if request.endpoint in ("auth.login", "auth.logout"):
            return redirect(url_for("dashboard.index"))

        return None

    @app.before_request
    def _session_timeout():

        session.permanent = True

    # --------------------------------------------------
    # SECURITY HEADERS
    # --------------------------------------------------

    @app.after_request
    def _headers(response):

        return apply_security_headers(
            response,
            https_only=app.config["SESSION_COOKIE_SECURE"]
        )

    # --------------------------------------------------
    # ERROR HANDLERS
    # --------------------------------------------------

    _register_error_handlers(app)

    # --------------------------------------------------
    # CLI COMMANDS
    # --------------------------------------------------

    from .cli import register_cli

    register_cli(app)

    # --------------------------------------------------
    # FAVICON
    # --------------------------------------------------

    @app.route("/favicon.ico")
    @limiter.exempt
    def favicon():

        return send_from_directory(
            Path(app.static_folder) / "img",
            "favicon.ico",
            mimetype="image/x-icon",
            max_age=86400
        )

    # --------------------------------------------------
    # HEALTH CHECK
    # --------------------------------------------------

    @app.route("/healthz")
    @limiter.exempt
    def healthz():

        return jsonify(status="ok")

    return app


def _register_error_handlers(app):

    messages = {
        400: "The request could not be processed.",
        401: "Please log in.",
        403: "You do not have permission to do that.",
        404: "Page not found.",
        405: "Method not allowed.",
        413: "The request is too large.",
        429: "Too many requests. Please wait a minute and try again.",
        500: "Something went wrong. The error has been logged.",
        503: "Service temporarily unavailable."
    }

    def render(code, message=None):

        if "/api/" in request.path:
            return jsonify(
                error=message or messages[code]
            ), code

        return render_template(
            "error.html",
            code=code,
            message=message or messages[code]
        ), code

    for code in messages:

        app.register_error_handler(
            code,
            lambda e, code=code: render(code)
        )

    @app.errorhandler(CSRFError)
    def _csrf(e):

        from .services.audit import log_event

        log_event(
            "csrf_rejected",
            "denied",
            details=request.path
        )

        return render(
            400,
            "The form expired or was not sent from this site. Please reload the page and try again."
        )

    @app.errorhandler(429)
    def _rate_limited(e):

        from .services.audit import log_event

        log_event(
            "rate_limited",
            "denied",
            details=request.path
        )

        return render(429)

    @app.errorhandler(Exception)
    def _unexpected(e):

        from werkzeug.exceptions import HTTPException

        if isinstance(e, HTTPException):
            return render(
                e.code if e.code in messages else 400
            )

        db.session.rollback()

        app.logger.exception(
            "Unhandled error on %s",
            request.path
        )

        return render(500)