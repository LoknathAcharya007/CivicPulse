"""Dashboard and town explorer: summaries of the user's permitted towns (real database values only)."""
from flask import Blueprint, current_app, render_template
from flask_login import current_user

from .. import charts
from ..constants import CATEGORIES, GRADE_NAMES, GRADE_ORDER, PRIORITY_ORDER, TOWN_CLASSES
from ..models import Asset, CostEstimate, Town
from ..security import accessible_town_ids, accessible_towns
from ..services.assets import (
    estimates_for_user,
    latest_assessments,
    recent_assessments,
    summary,
    town_overview,
)
from . import require_login_for
from .params import choice_arg, text_arg

bp = Blueprint("dashboard", __name__)
require_login_for(bp)

CATEGORY_CSS = {c: f"fill-cat-{i + 1}" for i, c in enumerate(CATEGORIES)}


@bp.route("/")
def index():
    towns = accessible_towns(current_user)
    stats = summary(current_user)
    town_ids = accessible_town_ids(current_user)
    assets = Asset.query.filter(Asset.town_id.in_(town_ids)).order_by(Asset.asset_code).all()
    latest = latest_assessments(assets)
    critical = [(a, latest[a.id]) for a in assets if latest[a.id] and latest[a.id].predicted_priority == "Critical"]
    by_grade = {g: n for g, _, n in stats["by_grade"]}
    priority = dict(stats["by_priority"])
    estimate_count = CostEstimate.query.join(Asset).filter(Asset.town_id.in_(town_ids)).count()
    ml = current_app.extensions["civicpulse_ml"]
    return render_template(
        "dashboard.html", towns=towns, stats=stats, critical=critical[:6], critical_total=len(critical),
        category_rows=charts.bar_rows([(c, n, CATEGORY_CSS[c]) for c, n in stats["by_category"]]),
        grade_stack=charts.stack([(f"{g} - {GRADE_NAMES[g]}", by_grade.get(g, 0), f"fill-{g}")
                                  for g in reversed(GRADE_ORDER)]),
        priority_rows=charts.bar_rows([(p, priority.get(p, 0), f"fill-{charts.SLUG[p]}")
                                       for p in reversed(PRIORITY_ORDER)]),
        poor=by_grade.get("D", 0) + by_grade.get("E", 0),
        urgent=priority.get("Critical", 0) + priority.get("High", 0),
        recent_assessments=recent_assessments(current_user, 6), recent=estimates_for_user(current_user, limit=6),
        estimate_count=estimate_count, model=ml.status(), total_towns=Town.query.count())


@bp.route("/towns/")
def towns():
    """Town explorer: the permitted towns with district, class and real asset statistics."""
    text = text_arg("q", 40)
    town_class = choice_arg("class", TOWN_CLASSES)
    coalfield = choice_arg("coalfield", ["yes", "no"])
    rows = town_overview(current_user, text, town_class, None if coalfield is None else coalfield == "yes")
    for row in rows:
        row["stack"] = charts.stack([(g, n, f"fill-{g}") for g, n in row["grades"].items()])
    return render_template("towns.html", rows=rows, permitted=len(accessible_town_ids(current_user)),
                           total_towns=Town.query.count(), town_classes=TOWN_CLASSES,
                           selected={"q": text or "", "class": town_class, "coalfield": coalfield},
                           grades=list(reversed(GRADE_ORDER)))
