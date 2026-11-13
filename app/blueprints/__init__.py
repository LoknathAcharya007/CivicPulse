"""Blueprints: one module per area of the application."""
from flask_login import login_required


def require_login_for(bp):
    """Every route in the blueprint needs a logged-in user (checked again per view where it matters)."""
    @bp.before_request
    @login_required
    def _login_gate():
        return None


def register_blueprints(app):
    from . import admin, analytics, assets, auth, dashboard, estimates, reports
    app.register_blueprint(auth.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(assets.bp, url_prefix="/assets")
    app.register_blueprint(estimates.bp, url_prefix="/estimates")
    app.register_blueprint(reports.bp, url_prefix="/reports")
    app.register_blueprint(analytics.bp, url_prefix="/analytics")
    app.register_blueprint(admin.bp, url_prefix="/admin")
