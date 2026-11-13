"""Authentication: login (rate limited), logout. Accounts are created only by administrators."""
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from ..extensions import db, limiter
from ..forms import ActionForm, LoginForm
from ..models import User, utcnow
from ..security import burn_verification_time, hash_password, is_safe_next_url, password_needs_rehash, verify_password
from ..services.audit import log_event

bp = Blueprint("auth", __name__)


def _username_key():
    return "login-user:" + (request.form.get("username") or "").strip().lower()[:40]


def _failed_login(response):
    """Only failed attempts count (a successful login redirects with 302), so switching demo accounts
    is never blocked, while password guessing is."""
    return response.status_code != 302


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit(lambda: current_app.config["LOGIN_RATE_LIMIT"], methods=["POST"], deduct_when=_failed_login)
@limiter.limit("20 per hour", key_func=_username_key, methods=["POST"], deduct_when=_failed_login)
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data.strip().lower()
        user = User.query.filter_by(username=username).first()
        if user is None:
            burn_verification_time(form.password.data)  # same delay as a real check
            valid = False
        else:
            valid = verify_password(user.password_hash, form.password.data)
        if valid and user.is_active:
            if password_needs_rehash(user.password_hash):
                user.password_hash = hash_password(form.password.data)
            session.clear()  # new session after login (prevents session fixation)
            login_user(user)
            session.permanent = True
            user.last_login_at = utcnow()
            db.session.commit()
            log_event("login", "success", "user", user.id, user=user)
            target = request.args.get("next")
            return redirect(target if is_safe_next_url(target) else url_for("dashboard.index"))
        reason = "inactive account" if valid else "wrong username or password"
        log_event("login_failed", "failure", "user", getattr(user, "id", None), f"username={username}; {reason}")
        flash("Invalid username or password.", "error")  # same message for every failure
    return render_template("auth/login.html", form=form)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    if not ActionForm().validate_on_submit():
        return redirect(url_for("dashboard.index"))
    log_event("logout", "success", "user", current_user.id)
    logout_user()
    session.clear()
    flash("You have been logged out.", "ok")
    return redirect(url_for("auth.login"))
