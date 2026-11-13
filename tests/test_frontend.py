"""Phase 3 frontend integration tests: every page is driven by real backend data, keeps the same permissions,
and follows the frontend security rules (no inline scripts/styles, no innerHTML, escaped output)."""
import re
from pathlib import Path

import pandas as pd
import pytest
from conftest import asset_in, csrf_from

from app import charts
from app.extensions import db
from app.models import Asset, CostEstimate, Town

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "ml_artifacts"


def _save(client, asset_id, target):
    token = csrf_from(client.get(f"/estimates/asset/{asset_id}").get_data(as_text=True))
    r = client.post(f"/estimates/asset/{asset_id}", data={"csrf_token": token, "target_grade": target})
    assert r.status_code == 302
    return int(r.headers["Location"].rsplit("/", 1)[1])


# ------------------------------------------------------------------ analytics: real Phase 1 figures only
def test_analytics_shows_phase1_metrics_and_synthetic_notice(app, andal_client):
    page = andal_client.get("/analytics/").get_data(as_text=True)
    m = app.extensions["civicpulse_ml"].spec["metrics"]
    assert "Synthetic data." in page and "do <strong>not</strong> establish real-world accuracy" in page
    assert f'{m["linear_regression"]["Linear Regression (test)"]["R2"]:.3f}' in page            # 0.915
    assert f'{m["linear_regression"]["Linear Regression (held-out towns)"]["R2"]:.3f}' in page   # 0.949
    assert f'{m["decision_tree"]["Decision Tree (test)"]["accuracy"] * 100:.1f}%' in page       # 76.4%
    for g in app.extensions["civicpulse_ml"].spec["group_names"].values():
        assert g in page
    assert page.count('class="dot dot-test"') == 318 and page.count('class="dot dot-holdout"') == 194


def test_analytics_per_class_matches_phase1_report(app):
    a = app.extensions["civicpulse_ml"].analytics()
    report = (ARTIFACTS / "evaluation_report.md").read_text(encoding="utf-8")
    for row in a["per_class"]["test"]:
        assert f'| {row["class"]} | {row["precision"]:g} | {row["recall"]:g} |' in report.replace(".0 |", " |")
    assert sum(map(sum, a["confusion"]["test"])) == 533 and sum(map(sum, a["confusion"]["held-out towns"])) == 306


def test_analytics_scatter_uses_saved_predictions(app):
    a = app.extensions["civicpulse_ml"].analytics()
    pred = pd.read_csv(ARTIFACTS / "predictions_test_and_holdout.csv").dropna(subset=["cost_inr"])
    first = pred[pred["split"] == "test"].iloc[0]
    assert a["scatter"]["test"][0] == (round(first["cost_inr"] / 1e5, 3), round(first["predicted_cost_inr"] / 1e5, 3))


def test_analytics_live_distribution_respects_town_access(app, andal_client):
    page = andal_client.get("/analytics/").get_data(as_text=True)
    assert "20 latest assessments" in page  # Andal 10 + Ukhra 10, not all 120


# ------------------------------------------------------------------ estimate history, CSV, picker, preselection
def test_estimate_history_filters_and_csv(app, admin_client):
    c_asset = asset_in(app, "Andal", grade="C")
    d_asset = asset_in(app, "Asansol", grade="D")
    _save(admin_client, c_asset, "A")
    _save(admin_client, d_asset, "B")
    with app.app_context():
        asansol = Town.query.filter_by(name="Asansol").one().id
        code = db.session.get(Asset, d_asset).asset_code
    assert "2 estimates" in admin_client.get("/estimates/").get_data(as_text=True)
    assert "1 estimate<" in admin_client.get("/estimates/?target=B").get_data(as_text=True)
    assert "1 estimate<" in admin_client.get(f"/estimates/?town={asansol}").get_data(as_text=True)
    assert "1 estimate<" in admin_client.get(f"/estimates/?q={code}").get_data(as_text=True)
    assert "0 estimates" in admin_client.get("/estimates/?q=nothing-matches").get_data(as_text=True)
    csv_text = admin_client.get("/reports/estimates.csv?target=B").get_data(as_text=True)
    assert len(csv_text.strip().splitlines()) == 2 and code in csv_text
    for bad in ["target=Z", "category=Bridges", "town=abc", "page=-1"]:
        assert admin_client.get(f"/estimates/?{bad}").status_code == 400, bad


def test_estimate_filters_cannot_reach_other_towns(app, andal_client):
    with app.app_context():
        asansol = Town.query.filter_by(name="Asansol").one().id
    assert andal_client.get(f"/estimates/?town={asansol}").status_code == 404
    assert andal_client.get(f"/reports/estimates.csv?town={asansol}").status_code == 404
    assert andal_client.get(f"/estimates/new?town={asansol}").status_code == 404


def test_asset_picker_lists_only_permitted_assets(app, andal_client):
    page = andal_client.get("/estimates/new").get_data(as_text=True)
    assert "20 assets found" in page and "PB-ASA-" not in page
    with app.app_context():
        a_grade = Asset.query.join(Town).filter(Town.name == "Andal", Asset.current_grade == "A").first()
    assert f"/estimates/asset/{a_grade.id}\"" not in page  # top-grade assets cannot be selected


@pytest.mark.parametrize("target,expected", [("A", "A"), ("b", "B"), ("E", "B"), ("C", "B"), ("<x>", "B"), ("", "B")])
def test_compare_preselection_is_validated(app, andal_client, target, expected):
    asset = asset_in(app, "Andal", grade="C")
    page = andal_client.get(f"/estimates/asset/{asset}?target={target}").get_data(as_text=True)
    checked = re.findall(r'<input checked[^>]*value="([A-E])"|<input[^>]*value="([A-E])"[^>]*checked', page)
    assert [a or b for a, b in checked] == [expected]  # invalid or downgrade targets fall back to the nearest upgrade


def test_saved_estimate_page_matches_database(app, andal_client):
    estimate_id = _save(andal_client, asset_in(app, "Andal", grade="C"), "B")
    page = andal_client.get(f"/estimates/{estimate_id}").get_data(as_text=True)
    with app.app_context():
        e = db.session.get(CostEstimate, estimate_id)
        from app.formatting import format_inr
        assert format_inr(e.final_cost) in page and format_inr(e.ml_base_cost) in page
        assert e.created_by.full_name in page and "IST" in page


# ------------------------------------------------------------------ pages, assets and frontend security rules
PAGES = ["/", "/towns/", "/assets/", "/estimates/", "/estimates/new", "/analytics/", "/reports/",
         "/admin/users", "/admin/users/new", "/admin/audit", "/admin/cost-references", "/admin/system"]


def test_every_page_has_title_description_and_no_inline_code(app, admin_client):
    asset = asset_in(app, "Andal", grade="C")
    estimate_id = _save(admin_client, asset, "A")
    titles = set()
    for url in PAGES + [f"/assets/{asset}", f"/estimates/asset/{asset}", f"/estimates/{estimate_id}",
                        f"/assets/{asset}/inspections/new", f"/assets/{asset}/improvements/new"]:
        page = admin_client.get(url).get_data(as_text=True)
        title = re.search(r"<title>(.*?)</title>", page).group(1)
        assert title.endswith("· CivicPulse") and title not in titles, url
        titles.add(title)
        assert re.search(r'<meta name="description" content="[^"]{20,}">', page), url
        assert not re.search(r"<script(?![^>]*\bsrc=)", page), url        # no inline <script> blocks (CSP)
        assert " style=" not in page and "<style" not in page, url          # no inline styles (CSP)
        assert re.search(r' on[a-z]+="', page) is None, url                 # no inline event handlers
    login_page = admin_client.application.test_client().get("/login").get_data(as_text=True)
    assert "<title>Sign in · CivicPulse</title>" in login_page


def test_javascript_never_uses_innerhtml_or_calculates_costs():
    js = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    js = re.sub(r"^\s*//.*$", "", js, flags=re.M)  # ignore comments; check the code itself
    assert "innerHTML" not in js and "outerHTML" not in js and "insertAdjacentHTML" not in js
    assert "eval(" not in js and "fetch(" not in js  # no client-side API calls or cost arithmetic
    assert not re.search(r"\b(final_cost|ml_base_cost|contingency)\b", js)


def test_official_navigation_has_no_admin_link(andal_client):
    page = andal_client.get("/").get_data(as_text=True)
    nav = page.split('id="site-nav"')[1].split("</nav>")[0]
    assert "/admin/" not in nav and "Analytics" in nav and "New estimate" in nav


def test_static_assets_and_favicon(client):
    for path, kind in [("/favicon.ico", "image/"), ("/static/img/favicon.svg", "image/svg"),
                       ("/static/css/app.css", "text/css"), ("/static/js/app.js", "javascript"),
                       ("/static/fonts/ibm-plex-sans-latin-ext-500-normal.woff2", "font/woff2")]:
        r = client.get(path)
        assert r.status_code == 200 and kind in r.mimetype, path


def test_town_search_is_escaped_and_parameterised(admin_client):
    page = admin_client.get("/towns/?q=%27%20OR%201%3D1%20--").get_data(as_text=True)
    assert "Showing 0 towns" in page
    page = admin_client.get("/towns/?q=<script>alert(1)</script>").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in page


# ------------------------------------------------------------------ chart geometry
def test_chart_helpers():
    rows = charts.bar_rows([("a", 5, "x"), ("b", 10, "y"), ("c", 0, "z")])
    assert [r["pct"] for r in rows] == [50.0, 100.0, 0]
    st = charts.stack([("A", 1, "a"), ("B", 3, "b")])
    assert st["total"] == 4 and st["pieces"][1]["x"] == 25.0 and sum(p["w"] for p in st["pieces"]) == pytest.approx(100)
    sc = charts.scatter_log({"s": [(1, 1), (100, 50)]})
    for p in sc["points"]["s"]:
        assert sc["left"] <= p["x"] <= sc["right"] and sc["top"] <= p["y"] <= sc["bottom"]
    assert charts.heat_class(0, 10) == "cm-0" and charts.heat_class(10, 10) == "cm-5"
    div = charts.diverging([("up", 50), ("down", -25)])
    assert div[0]["x"] == 50 and div[1]["x"] + div[1]["w"] == pytest.approx(50)
