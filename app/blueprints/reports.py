"""Reports: per-town summary and a CSV export of saved estimates (only the user's permitted towns)."""
import csv
import io

from flask import Blueprint, Response, render_template
from flask_login import current_user

from .. import charts
from ..services.assets import filter_estimates, summary, town_report
from ..services.audit import log_event
from . import require_login_for
from .params import estimate_filters

bp = Blueprint("reports", __name__)
require_login_for(bp)


def safe_cell(value):
    """Stop spreadsheet formula injection: text starting with = + - @ (or tab/CR) is prefixed with '."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@bp.route("/")
def index():
    rows = town_report(current_user)
    for row in rows:
        row["stack"] = charts.stack([("Grade D or E", row["poor_or_very_bad"], "fill-E"),
                                     ("Grade A to C", row["assets"] - row["poor_or_very_bad"], "fill-muted")])
    spend = charts.bar_rows([(r["town"].name, r["estimate_total"], "fill-pos") for r in rows if r["estimate_total"]])
    return render_template("reports/index.html", rows=rows, stats=summary(current_user), spend=spend,
                           totals={"assets": sum(r["assets"] for r in rows),
                                   "poor": sum(r["poor_or_very_bad"] for r in rows),
                                   "urgent": sum(r["high_or_critical"] for r in rows),
                                   "estimated": sum(r["estimated_assets"] for r in rows),
                                   "spend": sum(r["estimate_total"] for r in rows)})


@bp.route("/estimates.csv")
def estimates_csv():
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["estimate_id", "created_at_utc", "created_by", "town", "asset_code", "asset_name",
                     "category", "quantity", "unit", "current_grade", "target_grade", "ml_base_cost_inr",
                     "price_adjustment_inr", "contingency_percent", "contingency_inr", "final_cost_inr",
                     "reference_rate_check_inr", "predicted_priority", "illustrative_asset"])
    f = estimate_filters()  # same filters as the estimate history page
    rows = filter_estimates(current_user, f["text"], f["town_id"], f["category"], f["target"]).all()
    for e in rows:
        writer.writerow([safe_cell(v) for v in (
            e.id, e.created_at.isoformat(timespec="seconds"), e.created_by.username, e.asset.town.name,
            e.asset.asset_code, e.asset.name, e.asset.category, e.quantity, e.unit, e.current_grade,
            e.target_grade, f"{e.ml_base_cost:.2f}", f"{e.price_adjustment:.2f}", e.contingency_percent,
            f"{e.contingency_amount:.2f}", f"{e.final_cost:.2f}", f"{e.reference_rate_cost:.2f}",
            e.predicted_priority, "yes" if e.asset.is_illustrative else "no")])
    log_event("estimates_exported", "success", details=f"{len(rows)} rows")
    return Response(buffer.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=civicpulse_estimates.csv"})
