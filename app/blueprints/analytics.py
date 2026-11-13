"""Analytics: how the three Phase 1 models behave, shown from the verified Phase 1 outputs.

All figures come from ml_artifacts (feature_spec.json metrics, saved predictions, cluster profiles).
Nothing is retrained or re-scored here. The data is synthetic; these are not real-world accuracy figures."""
from flask import Blueprint, current_app, render_template
from flask_login import current_user

from .. import charts
from ..constants import PRIORITY_ORDER
from ..services.assets import model_distribution
from . import require_login_for

bp = Blueprint("analytics", __name__)
require_login_for(bp)

SPLIT_LABELS = {"test": "Test set", "held-out towns": "Held-out towns"}


@bp.route("/")
def index():
    ml = current_app.extensions["civicpulse_ml"]
    a = ml.analytics()
    m = a["metrics"]
    scatter = charts.scatter_log({k: a["scatter"][k] for k in ("test", "held-out towns") if k in a["scatter"]})
    effects = [c for c in a["coefficients"] if not c["elasticity"]]
    elasticity = next(c for c in a["coefficients"] if c["elasticity"])
    confusion = {}
    for split, matrix in a["confusion"].items():
        top = max(max(row) for row in matrix)
        confusion[split] = [[{"n": n, "css": charts.heat_class(n, top), "diag": i == j} for j, n in enumerate(row)]
                            for i, row in enumerate(matrix)]
    priority_live, groups_live = model_distribution(current_user)
    profiles = a["profiles"]
    group_rows = charts.bar_rows([(g["name"], groups_live.get(g["group"], 0), f"fill-{['E', 'A', 'C', 'D'][g['group']]}"
                                   if g["group"] < 4 else "fill-pos") for g in profiles])
    return render_template(
        "analytics/index.html", a=a, m=m, scatter=scatter, split_labels=SPLIT_LABELS,
        effects=charts.diverging([(c["label"], c["effect_pct"]) for c in effects]), elasticity=elasticity,
        importance=charts.bar_rows([(r["label"], r["importance"], "fill-pos") for r in a["importance"]]),
        confusion=confusion, order=PRIORITY_ORDER,
        elbow=charts.line(a["inertias"], list(range(1, len(a["inertias"]) + 1)), highlight=a["k"]),
        profiles=profiles, group_rows=group_rows,
        priority_rows=charts.bar_rows([(p, priority_live.get(p, 0), f"fill-{charts.SLUG[p]}")
                                       for p in reversed(PRIORITY_ORDER)]),
        live_total=sum(priority_live.values()))
