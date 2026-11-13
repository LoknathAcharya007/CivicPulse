"""Cost estimates: compare every upgrade scenario for an asset, save one, list and view saved estimates.

The browser sends ONLY the target grade. Quantity, grades, rates, contingency and every amount are
taken from the database and computed on the server, so they cannot be manipulated in the request."""
from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from ..constants import CATEGORIES, GRADE_ORDER, upgrade_options
from ..extensions import limiter
from ..forms import EstimateForm, grade_choices
from ..security import accessible_towns, get_asset_or_404, get_estimate_or_404
from ..services.assets import ValidationError, filter_estimates, save_estimate, search_assets
from ..services.audit import log_event
from ..services.costing import CostingError
from . import require_login_for
from .params import asset_filters, estimate_filters, int_arg

bp = Blueprint("estimates", __name__)
require_login_for(bp)


def _estimator():
    return current_app.extensions["civicpulse_estimator"]


class EstimatesPaused(Exception):
    """Reference data no longer matches the Phase 1 models, so new estimates are refused."""


PAUSED_MESSAGE = ("New estimates are paused: the cost reference rates in the database no longer match the rates "
                  "the Phase 1 model was trained with. Saved estimates are unaffected. An administrator must "
                  "restore the Phase 1 rates (or retrain the models in Colab).")


@bp.errorhandler(EstimatesPaused)
def _paused(exc):
    log_event("estimates_paused", "failure", details=str(exc)[:500])
    if "/api/" in request.path:
        return jsonify(error=PAUSED_MESSAGE), 503
    return render_template("error.html", code=503, message=PAUSED_MESSAGE), 503


def _scenarios(asset):
    latest = asset.latest_assessment()
    if latest is None:
        abort(404)
    try:
        return latest, _estimator().compare(asset, latest)
    except CostingError as exc:
        current_app.logger.error("Costing unavailable: %s", exc)
        raise EstimatesPaused(str(exc)) from exc


@bp.route("/")
def index():
    """Saved estimate history with search and filters (permitted towns only)."""
    f = estimate_filters()
    page = int_arg("page") or 1
    results = filter_estimates(current_user, f["text"], f["town_id"], f["category"], f["target"]).paginate(
        page=page, per_page=20, error_out=False)
    return render_template("estimates/index.html", results=results, towns=accessible_towns(current_user),
                           categories=CATEGORIES, grades=list(reversed(GRADE_ORDER)),
                           selected={"town": f["town_id"], "category": f["category"], "target": f["target"],
                                     "q": f["text"] or ""})


@bp.route("/new")
def new():
    """Step 1 of an estimate: choose an asset (same filters and permissions as the asset explorer)."""
    f = asset_filters()
    page = int_arg("page") or 1
    results = search_assets(current_user, f["town_id"], f["category"], f["grade"], f["text"], page)
    return render_template("estimates/new.html", results=results, towns=accessible_towns(current_user),
                           categories=CATEGORIES, grades=list(reversed(GRADE_ORDER)),
                           selected={"town": f["town_id"], "category": f["category"], "grade": f["grade"],
                                     "q": f["text"] or ""})


def _render_compare(asset, form, status=200):
    latest, scenarios = _scenarios(asset)
    options = [s["target_grade"] for s in scenarios]
    if form.target_grade.data not in options:
        form.target_grade.data = options[0] if options else None
    return render_template("estimates/compare.html", asset=asset, latest=latest, scenarios=scenarios, form=form,
                           selected=form.target_grade.data), status


@bp.route("/asset/<int:asset_id>", methods=["GET"])
def compare(asset_id):
    """Steps 2-3: every valid upgrade with the backend's breakdown; ?target= preselects one (validated)."""
    asset = get_asset_or_404(asset_id)
    form = EstimateForm()
    form.target_grade.choices = grade_choices(upgrade_options(asset.current_grade))
    form.target_grade.data = (request.args.get("target") or "").strip().upper()[:1] or None
    return _render_compare(asset, form)


@bp.route("/asset/<int:asset_id>", methods=["POST"])
@limiter.limit(lambda: current_app.config["ESTIMATE_RATE_LIMIT"])
def create(asset_id):
    asset = get_asset_or_404(asset_id)
    form = EstimateForm()
    form.target_grade.choices = grade_choices(upgrade_options(asset.current_grade))
    if not form.validate_on_submit():
        # Not one of the better grades (downgrade, same grade or invalid value): rejected.
        log_event("estimate_rejected", "failure", "asset", asset.id,
                  f"target={str(form.target_grade.data)[:5]!r} current={asset.current_grade}")
        flash("Choose a target grade that is better than the current grade.", "error")
        return _render_compare(asset, form, 400)
    try:
        estimate = save_estimate(asset, current_user, _estimator(), form.target_grade.data)
    except ValidationError as exc:
        flash(str(exc), "error")
        return redirect(url_for("assets.detail", asset_id=asset.id))
    except CostingError as exc:
        current_app.logger.error("Estimate not saved: %s", exc)
        raise EstimatesPaused(str(exc)) from exc
    flash("Estimate saved.", "ok")
    return redirect(url_for("estimates.detail", estimate_id=estimate.id))


@bp.route("/<int:estimate_id>")
def detail(estimate_id):
    estimate = get_estimate_or_404(estimate_id)
    ml = current_app.extensions["civicpulse_ml"]
    return render_template("estimates/detail.html", estimate=estimate,
                           group_name=ml.group_name(estimate.predicted_group),
                           lines={line["key"]: line for line in estimate.breakdown["lines"]})


@bp.route("/api/asset/<int:asset_id>/scenarios")
def api_scenarios(asset_id):
    """JSON version of the comparison. Same access rules as the page."""
    asset = get_asset_or_404(asset_id)
    latest, scenarios = _scenarios(asset)
    return jsonify({
        "asset": {"id": asset.id, "code": asset.asset_code, "category": asset.category, "unit": asset.unit,
                  "quantity": asset.quantity, "town": asset.town.name, "current_grade": asset.current_grade,
                  "illustrative": asset.is_illustrative},
        "assessment": {"id": latest.id, "assessed_on": latest.assessed_on.isoformat()},
        "scenarios": [{k: s[k] for k in ("current_grade", "target_grade", "grades_gained", "ml_base_cost",
                                         "price_adjustment", "contingency_percent", "contingency_amount",
                                         "final_cost", "predicted_priority", "predicted_group_name", "lines")}
                      | {"reference_check_cost": s["reference_check"]["cost"]} for s in scenarios],
    })
