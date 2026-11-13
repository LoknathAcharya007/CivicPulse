"""Infrastructure assets: browse, filter, details, history, new inspections and completed improvements."""
from datetime import date

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..constants import CATEGORIES, GRADE_ORDER, upgrade_options
from ..forms import ImprovementForm, InspectionForm, grade_choices
from ..security import accessible_towns, get_asset_or_404
from ..services.assets import ValidationError, latest_assessments, record_improvement, record_inspection, search_assets
from . import require_login_for
from .params import asset_filters, int_arg

bp = Blueprint("assets", __name__)
require_login_for(bp)


@bp.route("/")
def index():
    f = asset_filters()
    page = int_arg("page") or 1
    results = search_assets(current_user, f["town_id"], f["category"], f["grade"], f["text"], page)
    latest = latest_assessments(results.items)
    return render_template("assets/index.html", results=results, latest=latest,
                           towns=accessible_towns(current_user), categories=CATEGORIES,
                           grades=list(reversed(GRADE_ORDER)),
                           selected={"town": f["town_id"], "category": f["category"], "grade": f["grade"],
                                     "q": f["text"] or ""})


@bp.route("/<int:asset_id>")
def detail(asset_id):
    asset = get_asset_or_404(asset_id)
    latest = asset.latest_assessment()
    ml = current_app.extensions["civicpulse_ml"]
    profile = None
    if latest and latest.predicted_group is not None:
        profile = next((g for g in ml.spec["group_profiles"] if g["group"] == latest.predicted_group), None)
    return render_template("assets/detail.html", asset=asset, latest=latest,
                           group_name=ml.group_name(latest.predicted_group) if latest else None, profile=profile,
                           assessments=asset.assessments.all(), maintenance=asset.maintenance.all(),
                           estimates=asset.estimates.limit(10).all(), options=upgrade_options(asset.current_grade),
                           grades=GRADE_ORDER)


@bp.route("/<int:asset_id>/inspections/new", methods=["GET", "POST"])
def new_inspection(asset_id):
    asset = get_asset_or_404(asset_id)
    form = InspectionForm()
    status = 200
    if request.method == "GET":
        form.assessed_on.data = date.today()
    elif form.validate_on_submit():
        try:
            record_inspection(asset, current_user, current_app.extensions["civicpulse_ml"],
                              form.assessed_on.data, form.grade.data, form.damaged_share_pct.data,
                              form.years_since_maintenance.data, form.complaints_last_12m.data,
                              (form.notes.data or "").strip())
            flash("Inspection recorded.", "ok")
            return redirect(url_for("assets.detail", asset_id=asset.id))
        except ValidationError as exc:
            flash(str(exc), "error")
        status = 400
    else:
        status = 400
    return render_template("assets/inspection_form.html", asset=asset, form=form), status


@bp.route("/<int:asset_id>/improvements/new", methods=["GET", "POST"])
def new_improvement(asset_id):
    asset = get_asset_or_404(asset_id)
    options = upgrade_options(asset.current_grade)
    if not options:
        flash("This asset is already grade A (Very Good); no improvement can be recorded.")
        return redirect(url_for("assets.detail", asset_id=asset.id))
    form = ImprovementForm()
    form.to_grade.choices = grade_choices(options)  # browser can only pick better grades
    status = 200
    if request.method == "GET":
        form.performed_on.data = date.today()
    else:
        if form.validate_on_submit():
            try:
                record_improvement(asset, current_user, current_app.extensions["civicpulse_ml"],
                                   form.performed_on.data, form.to_grade.data, form.damaged_share_pct.data,
                                   form.description.data.strip(), form.actual_cost_inr.data)
                flash("Improvement recorded.", "ok")
                return redirect(url_for("assets.detail", asset_id=asset.id))
            except ValidationError as exc:
                flash(str(exc), "error")
        status = 400
    return render_template("assets/improvement_form.html", asset=asset, form=form), status
