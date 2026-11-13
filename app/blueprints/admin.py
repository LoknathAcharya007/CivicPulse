"""Administration (administrators only): accounts, town permissions, audit log, cost references, model status."""
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from wtforms.fields.choices import SelectChoice

from ..constants import ROLE_ADMIN, ROLE_OFFICIAL
from ..extensions import db
from ..forms import PasswordResetForm, UserCreateForm, UserEditForm
from ..models import AuditLog, CostReference, Town, User
from ..security import MAX_ID, admin_required, hash_password, password_problems
from ..services.audit import log_event
from . import require_login_for

bp = Blueprint("admin", __name__)
require_login_for(bp)


@bp.before_request
@admin_required
def _admins_only():
    return None


def _town_choices():
    return [SelectChoice(t.id, t.name) for t in Town.query.order_by(Town.name)]


def _selected_towns(form):
    if form.role.data != ROLE_OFFICIAL:
        return []
    return Town.query.filter(Town.id.in_(form.towns.data or [])).all()


@bp.route("/users")
def users():
    return render_template("admin/users.html", users=User.query.order_by(User.username).all())


@bp.route("/users/new", methods=["GET", "POST"])
def new_user():
    form = UserCreateForm()
    form.towns.choices = _town_choices()
    if form.validate_on_submit():
        username = form.username.data.strip().lower()
        problems = password_problems(form.password.data, username)
        if User.query.filter_by(username=username).first():
            form.username.errors.append("Username already exists.")
        elif problems:
            form.password.errors.append("Password needs: " + ", ".join(problems))
        elif form.role.data == ROLE_OFFICIAL and not form.towns.data:
            form.towns.errors.append("Choose at least one town for an official.")
        else:
            user = User(username=username, full_name=form.full_name.data.strip(), role=form.role.data,
                        password_hash=hash_password(form.password.data), towns=_selected_towns(form),
                        created_by_id=current_user.id)
            db.session.add(user)
            db.session.commit()
            log_event("user_created", "success", "user", user.id,
                      f"{username} role={user.role} towns={[t.name for t in user.towns]}")
            flash(f"Account '{username}' created.", "ok")
            return redirect(url_for("admin.users"))
    return render_template("admin/user_form.html", form=form, title="Create account"), \
        (400 if request.method == "POST" else 200)


@bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
def edit_user(user_id):
    user = (0 < user_id <= MAX_ID and db.session.get(User, user_id)) or abort(404)
    form = UserEditForm(obj=user if request.method == "GET" else None)
    form.towns.choices = _town_choices()
    if request.method == "GET":
        form.towns.data = [t.id for t in user.towns]
    if form.validate_on_submit():
        if user.id == current_user.id and (not form.active.data or form.role.data != ROLE_ADMIN):
            flash("You cannot deactivate or demote your own account.", "error")
        elif form.role.data == ROLE_OFFICIAL and not form.towns.data:
            form.towns.errors.append("Choose at least one town for an official.")
        else:
            security_change = (user.role != form.role.data or user.active != form.active.data
                               or {t.id for t in user.towns} != set(form.towns.data or []))
            user.full_name = form.full_name.data.strip()
            user.role = form.role.data
            user.active = form.active.data
            user.towns = _selected_towns(form)
            if security_change:
                user.session_version += 1  # forces the user to log in again with the new permissions
            db.session.commit()
            log_event("user_updated", "success", "user", user.id,
                      f"role={user.role} active={user.active} towns={[t.name for t in user.towns]}")
            flash("Account updated.", "ok")
            return redirect(url_for("admin.users"))
    return render_template("admin/user_edit.html", form=form, user=user), (400 if request.method == "POST" else 200)


@bp.route("/users/<int:user_id>/password", methods=["GET", "POST"])
def reset_password(user_id):
    user = (0 < user_id <= MAX_ID and db.session.get(User, user_id)) or abort(404)
    form = PasswordResetForm()
    if form.validate_on_submit():
        problems = password_problems(form.password.data, user.username)
        if problems:
            form.password.errors.append("Password needs: " + ", ".join(problems))
        else:
            user.password_hash = hash_password(form.password.data)
            user.session_version += 1  # logs the user out everywhere
            db.session.commit()
            log_event("password_reset", "success", "user", user.id)
            flash(f"Password for '{user.username}' changed.", "ok")
            return redirect(url_for("admin.users"))
    return render_template("admin/password_form.html", form=form, user=user), \
        (400 if request.method == "POST" else 200)


@bp.route("/audit")
def audit():
    page = request.args.get("page", "1")
    page = int(page) if page.isdigit() and 0 < int(page) < 100000 else 1
    action = request.args.get("action", "").strip()[:50]
    query = AuditLog.query.order_by(AuditLog.id.desc())
    if action:
        query = query.filter(AuditLog.action == action)
    actions = [a for (a,) in db.session.query(AuditLog.action).distinct().order_by(AuditLog.action)]
    return render_template("admin/audit.html", logs=query.paginate(page=page, per_page=50, error_out=False),
                           action=action, actions=actions)


@bp.route("/cost-references")
def cost_references():
    return render_template("admin/cost_references.html", refs=CostReference.query.order_by(CostReference.id).all())


@bp.route("/system")
def system():
    estimator = current_app.extensions["civicpulse_estimator"]
    return render_template("admin/system.html", model=current_app.extensions["civicpulse_ml"].status(),
                           estimator=estimator, env=current_app.config["ENV_NAME"])
