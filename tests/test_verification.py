"""Phase 2 verification tests: route coverage, sessions, traceability, historical estimates, admin functions,
clear ML errors and audit coverage. Added during the backend verification pass."""
import hashlib
import json
import re
import shutil
import time
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from conftest import PASSWORDS, asset_in, csrf_from, login

from app import create_app
from app.constants import UNITS
from app.extensions import db
from app.formatting import format_ist, format_number, safe_http_url
from app.models import Asset, AuditLog, CostEstimate, CostReference, Town, User
from app.services.ml_service import ArtifactIntegrityError, ModelService

ARTIFACTS = Path(__file__).resolve().parent.parent / "ml_artifacts"


def _ids(app):
    with app.app_context():
        andal = Town.query.filter_by(name="Andal").one()
        own = Asset.query.filter_by(town_id=andal.id, current_grade="C").first().id
        return {"asset_id": own, "user_id": User.query.filter_by(username="off.andal").one().id, "town": andal.id}


def _save_estimate(client, asset_id, target):
    token = csrf_from(client.get(f"/estimates/asset/{asset_id}").get_data(as_text=True))
    r = client.post(f"/estimates/asset/{asset_id}", data={"csrf_token": token, "target_grade": target})
    assert r.status_code == 302, r.status_code
    return int(r.headers["Location"].rstrip("/").split("/")[-1])


# ------------------------------------------------------------------ every route is covered
ADMIN_GET_ROUTES = ["/", "/towns/", "/assets/", "/assets/{asset_id}", "/assets/{asset_id}/inspections/new",
                    "/assets/{asset_id}/improvements/new", "/estimates/", "/estimates/asset/{asset_id}",
                    "/estimates/{estimate_id}", "/estimates/api/asset/{asset_id}/scenarios", "/reports/",
                    "/reports/estimates.csv", "/admin/users", "/admin/users/new", "/admin/users/{user_id}/edit",
                    "/admin/users/{user_id}/password", "/admin/audit", "/admin/cost-references", "/admin/system",
                    "/healthz", "/login", "/estimates/new", "/analytics/", "/favicon.ico"]
POST_ONLY = {"/logout"}


def test_every_registered_route_is_in_the_test_list(app):
    rules = {re.sub(r"<int:(\w+)>", r"{\1}", r.rule) for r in app.url_map.iter_rules() if r.endpoint != "static"}
    assert rules == set(ADMIN_GET_ROUTES) | POST_ONLY


def test_every_get_route_works_for_admin(app, client):
    ids = _ids(app)
    login(client, "admin")
    ids["estimate_id"] = _save_estimate(client, ids["asset_id"], "A")
    for route in ADMIN_GET_ROUTES:
        r = client.get(route.format(**ids))
        expected = 302 if route == "/login" else 200  # logged-in users are sent on from /login
        assert r.status_code == expected, (route, r.status_code)


def test_every_post_route_requires_csrf(app, admin_client):
    ids = _ids(app)
    posts = [r for r in app.url_map.iter_rules() if "POST" in r.methods]
    assert len(posts) >= 8
    for rule in posts:
        url = re.sub(r"<int:(\w+)>", lambda m: str(ids.get(m.group(1), 1)), rule.rule)
        assert admin_client.post(url, data={"target_grade": "A"}).status_code == 400, url
    with app.app_context():
        assert AuditLog.query.filter_by(action="csrf_rejected").count() == len(posts)


@pytest.mark.parametrize("data", [{}, {"grade": "C"}, {"assessed_on": "2026-01-01"}])
def test_missing_inspection_fields_are_rejected(app, andal_client, data):
    asset = asset_in(app, "Andal")
    token = csrf_from(andal_client.get(f"/assets/{asset}/inspections/new").get_data(as_text=True))
    assert andal_client.post(f"/assets/{asset}/inspections/new", data={"csrf_token": token, **data}).status_code == 400


def test_missing_target_grade_is_rejected(app, andal_client):
    asset = asset_in(app, "Andal", grade="C")
    token = csrf_from(andal_client.get(f"/estimates/asset/{asset}").get_data(as_text=True))
    assert andal_client.post(f"/estimates/asset/{asset}", data={"csrf_token": token}).status_code == 400


def test_missing_user_fields_are_rejected(admin_client):
    token = csrf_from(admin_client.get("/admin/users/new").get_data(as_text=True))
    assert admin_client.post("/admin/users/new", data={"csrf_token": token}).status_code == 400


# ------------------------------------------------------------------ towns and dashboard data
def test_towns_page_lists_only_permitted_towns_with_correct_counts(app, andal_client):
    page = andal_client.get("/towns/").get_data(as_text=True)
    assert "Paschim Bardhaman" in page and page.count('class="card town-card') == 2  # Andal + Ukhra
    cards = page.split('class="card town-card')[1:]
    assert not any(">Asansol<" in c or ">Durgapur<" in c for c in cards)
    with app.app_context():
        andal = Town.query.filter_by(name="Andal").one()
        assets = Asset.query.filter_by(town_id=andal.id).all()
        grades = [a.current_grade for a in assets]
        pop = format_number(andal.population_2011)
    card = next(c for c in cards if ">Andal</a>" in c)
    assert f"<dd>{pop}</dd>" in card
    assert f"<strong>{len(assets)}</strong><span>Assets</span>" in card
    assert f"<strong>{grades.count('D') + grades.count('E')}</strong><span>Grade D / E</span>" in card
    for g in "ABCDE":  # legend of the stacked bar: real counts per grade
        assert f"{g} <strong>{grades.count(g)}</strong>" in card


def test_admin_towns_page_lists_all_11_towns(admin_client):
    page = admin_client.get("/towns/").get_data(as_text=True)
    assert "All 11 towns." in page and page.count('class="card town-card') == 11
    filtered = admin_client.get("/towns/?class=Municipal+corporation").get_data(as_text=True)
    assert filtered.count('class="card town-card') == 2  # Asansol and Durgapur
    assert admin_client.get("/towns/?class=Invalid").status_code == 400


def test_dashboard_numbers_match_database(app, andal_client):
    page = andal_client.get("/").get_data(as_text=True)
    with app.app_context():
        towns = [t.id for t in Town.query.filter(Town.name.in_(["Andal", "Ukhra"]))]
        assets = Asset.query.filter(Asset.town_id.in_(towns)).all()
        latest = [a.latest_assessment().predicted_priority for a in assets]
    kpis = re.findall(r'<div class="kpi-value">([^<]+)</div>', page)
    poor = sum(a.current_grade in "DE" for a in assets)
    assert kpis[:4] == ["2", str(len(assets)), str(poor), str(latest.count("High") + latest.count("Critical"))]
    assert f"{latest.count('Critical')} asset" in page.split('id="crit-h"')[1][:200]
    grade_table = page.split('id="cond-h"')[1].split("</table>")[0]
    for g in "ABCDE":
        n = sum(a.current_grade == g for a in assets)
        assert re.search(rf'grade-{g} ".*?</td><td class="num">{n}</td>', grade_table), g


# ------------------------------------------------------------------ sessions and rate limiting
def test_session_expires_after_idle_lifetime(app, client):
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(seconds=1)
    login(client, "off.andal")
    assert client.get("/").status_code == 200
    time.sleep(2.2)
    assert client.get("/").status_code == 302  # signed cookie older than the lifetime is rejected


def test_session_cookie_is_replaced_at_login(client):
    client.get("/login")
    before = client.get_cookie("civicpulse_session").value
    login(client, "off.andal")
    assert client.get_cookie("civicpulse_session").value != before


def test_successful_logins_do_not_count_towards_the_limit(app):
    """Switching demo accounts several times a minute must not lock the presenter out."""
    for _ in range(7):
        c = app.test_client()
        assert login(c, "off.andal").status_code == 302


def test_failed_logins_still_limited_after_successes(app):
    login(app.test_client(), "off.andal")
    codes = [login(app.test_client(), "off.andal", f"bad-{i}-pass").status_code for i in range(6)]
    assert codes[-1] == 429 and codes[:5] == [200] * 5


# ------------------------------------------------------------------ cost estimates: traceability and history
def test_estimate_is_traceable_to_inputs_rates_and_model(app, andal_client):
    asset = asset_in(app, "Andal", grade="C")
    estimate_id = _save_estimate(andal_client, asset, "A")
    ml = app.extensions["civicpulse_ml"]
    with app.app_context():
        e = db.session.get(CostEstimate, estimate_id)
        frame = pd.DataFrame([e.input_snapshot])
        assert round(float(ml.predict_cost(frame)[0]), 2) == e.ml_base_cost  # reproducible from stored inputs
        assert e.final_cost == pytest.approx(e.ml_base_cost + e.price_adjustment + e.contingency_amount, abs=0.01)
        assert e.contingency_amount == pytest.approx((e.ml_base_cost + e.price_adjustment) * e.contingency_percent
                                                     / 100, abs=0.01)
        rc = e.breakdown["reference_check"]
        assert sum(c["amount_per_asset_unit"] for c in rc["components"]) == pytest.approx(rc["rate_per_unit"], abs=0.05)
        assert rc["cost"] == pytest.approx(rc["rate_per_unit"] * e.quantity, abs=0.01)
        refs = {r.ref_id: r for r in CostReference.query.all()}
        for c in rc["components"]:
            ref = refs[c["ref_id"]]
            assert (c["rate_inr"], c["source_document"], c["document_date"]) == \
                (ref.rate_inr, ref.source_document, ref.document_date)
        assert e.model_manifest_sha256 == ml.manifest_sha256


def test_historical_estimate_unchanged_after_rate_update(app, andal_client):
    asset = asset_in(app, "Andal", grade="C")
    estimate_id = _save_estimate(andal_client, asset, "A")
    before = andal_client.get(f"/estimates/{estimate_id}").get_data(as_text=True)
    with app.app_context():
        saved = db.session.get(CostEstimate, estimate_id)
        snapshot = (saved.final_cost, json.dumps(saved.breakdown, sort_keys=True))
        used = saved.breakdown["reference_check"]["components"][0]["ref_id"]
        ref = CostReference.query.filter_by(ref_id=used).one()
        old_rate = ref.rate_inr
        ref.rate_inr = old_rate * 2  # someone edits a reference rate later
        db.session.commit()
    after = andal_client.get(f"/estimates/{estimate_id}").get_data(as_text=True)
    with app.app_context():
        saved = db.session.get(CostEstimate, estimate_id)
        assert (saved.final_cost, json.dumps(saved.breakdown, sort_keys=True)) == snapshot
    assert before.split("<h1>")[1] == after.split("<h1>")[1]  # the saved page is identical (after the flash line)
    # New estimates are refused with a clear message instead of mixing two price levels.
    r = andal_client.get(f"/estimates/asset/{asset}")
    assert r.status_code == 503 and "New estimates are paused" in r.get_data(as_text=True)
    api = andal_client.get(f"/estimates/api/asset/{asset}/scenarios")
    assert api.status_code == 503 and "paused" in api.get_json()["error"]


@pytest.mark.parametrize("category", list(UNITS))
def test_each_category_uses_its_unit_and_labels_assumptions(app, admin_client, category):
    with app.app_context():
        asset = Asset.query.filter(Asset.category == category, Asset.current_grade != "A").first()
        assert asset.unit == UNITS[category]
        asset_id = asset.id
    page = admin_client.get(f"/estimates/asset/{asset_id}").get_data(as_text=True)
    assert "not 2026 market prices" in page
    data = admin_client.get(f"/estimates/api/asset/{asset_id}/scenarios").get_json()
    assert data["asset"]["unit"] == UNITS[category]
    with app.app_context():
        a = db.session.get(Asset, asset_id)
        for target in [s["target_grade"] for s in data["scenarios"]]:
            r = app.extensions["civicpulse_estimator"].estimate(a, a.latest_assessment(), target)
            for c in r["reference_check"]["components"]:
                if c["verification_status"] == "Assumption":
                    assert c["rate_type"] == "Assumed composite rate"
                    assert "ASSUMPTION" in page
                else:
                    assert c["rate_type"] == "Composite work rate"
                    assert c["verification_status"] == "Official (third-party copy)"
                    assert "2023" in (c["price_basis"] or "") + (c["source_document"] or "")


def test_currency_on_pages_uses_indian_grouping(app, admin_client):
    asset = asset_in(app, "Asansol", not_grade="A")
    _save_estimate(admin_client, asset, "A")
    for url in [f"/estimates/asset/{asset}", "/estimates/", "/reports/", "/admin/cost-references"]:
        page = admin_client.get(url).get_data(as_text=True)
        amounts = re.findall(r"₹[\d,]+(?:\.\d+)?", page)
        assert amounts, url
        for amount in amounts:
            digits = amount[1:].split(".")[0]
            assert re.fullmatch(r"\d{1,3}|(\d{1,2},)?(\d{2},)*\d{3}", digits) or re.fullmatch(
                r"\d{1,2}(,\d{2})*,\d{3}", digits), (url, amount)


def test_estimate_with_inconsistent_condition_record_is_refused(app, andal_client):
    asset = asset_in(app, "Andal", grade="C")
    token = csrf_from(andal_client.get(f"/estimates/asset/{asset}").get_data(as_text=True))
    with app.app_context():
        a = db.session.get(Asset, asset)
        a.current_grade = "D"  # out of sync with the latest assessment (C)
        db.session.commit()
    r = andal_client.post(f"/estimates/asset/{asset}", data={"csrf_token": token, "target_grade": "B"})
    assert r.status_code in (302, 400)
    with app.app_context():
        assert CostEstimate.query.count() == 0


# ------------------------------------------------------------------ admin functions
def test_admin_password_reset_changes_password_and_ends_sessions(app, client):
    official = app.test_client()
    login(official, "off.andal")
    assert official.get("/").status_code == 200
    login(client, "admin")
    user_id = _ids(app)["user_id"]
    token = csrf_from(client.get(f"/admin/users/{user_id}/password").get_data(as_text=True))
    weak = client.post(f"/admin/users/{user_id}/password", data={"csrf_token": token, "password": "short",
                                                                 "confirm": "short"})
    assert weak.status_code == 400
    r = client.post(f"/admin/users/{user_id}/password", data={"csrf_token": token, "password": "Brand-new-pass-77",
                                                              "confirm": "Brand-new-pass-77"})
    assert r.status_code == 302
    assert official.get("/").status_code == 302  # old session ended
    assert login(app.test_client(), "off.andal").status_code == 200  # old password fails
    assert login(app.test_client(), "off.andal", "Brand-new-pass-77").status_code == 302
    with app.app_context():
        assert AuditLog.query.filter_by(action="password_reset").count() == 1


def test_admin_role_change_and_deactivation(app, admin_client):
    user_id = _ids(app)["user_id"]
    token = csrf_from(admin_client.get(f"/admin/users/{user_id}/edit").get_data(as_text=True))
    r = admin_client.post(f"/admin/users/{user_id}/edit", data={"csrf_token": token, "full_name": "Andal Official",
                                                                "role": "official", "towns": [_ids(app)["town"]]})
    assert r.status_code == 302  # 'active' unticked -> deactivated
    assert login(app.test_client(), "off.andal").status_code == 200  # cannot log in any more
    with app.app_context():
        assert db.session.get(User, user_id).active is False


def test_admin_pages_show_reference_and_model_information(app, admin_client):
    refs = admin_client.get("/admin/cost-references").get_data(as_text=True)
    assert refs.split("<tbody>")[1].count("<tr>") == 32 and "Unavailable" in refs and "ASSUMPTION" in refs
    system = admin_client.get("/admin/system").get_data(as_text=True)
    assert app.extensions["civicpulse_ml"].manifest_sha256 in system and "cost ✓, priority ✓, group ✓" in system


def test_audit_log_filter(app, admin_client):
    login(app.test_client(), "off.andal", "wrong-password-9")
    page = admin_client.get("/admin/audit?action=login_failed").get_data(as_text=True)
    table = page.split("<tbody>")[1]
    assert '<td data-label="Action">login_failed</td>' in table and '<td data-label="Action">login</td>' not in table


def test_key_actions_are_audited(app, client):
    login(client, "off.andal", "wrong-password-1")
    login(client, "off.andal")
    asset = asset_in(app, "Andal", grade="C")
    other = asset_in(app, "Asansol")
    _save_estimate(client, asset, "B")
    client.get(f"/assets/{other}")
    client.get("/admin/users")
    client.post("/logout", data={"csrf_token": csrf_from(client.get("/").get_data(as_text=True))})
    with app.app_context():
        actions = {a.action for a in AuditLog.query.all()}
        assert {"login_failed", "login", "estimate_saved", "record_access_denied", "admin_area_denied",
                "logout"} <= actions
        assert all(PASSWORDS["off.andal"] not in (a.details or "") for a in AuditLog.query.all())


# ------------------------------------------------------------------ ML: clear errors for bad artifacts
def _copy(tmp_path):
    folder = tmp_path / "artifacts"
    shutil.copytree(ARTIFACTS, folder)
    return folder


def _rehash(folder, name):
    manifest = json.loads((folder / "MANIFEST.json").read_text(encoding="utf-8"))
    manifest[name] = hashlib.sha256((folder / name).read_bytes()).hexdigest()
    (folder / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_unloadable_model_gives_clear_error(tmp_path):
    folder = _copy(tmp_path)
    (folder / "kmeans.joblib").write_bytes(b"this is not a pickle")
    _rehash(folder, "kmeans.joblib")  # checksum consistent, content broken
    with pytest.raises(ArtifactIntegrityError, match=r"kmeans.joblib could not be loaded.*Restore the original"):
        ModelService(folder)


def test_swapped_model_files_give_clear_error(tmp_path):
    folder = _copy(tmp_path)
    tree = (folder / "decision_tree.joblib").read_bytes()
    (folder / "decision_tree.joblib").write_bytes((folder / "kmeans.joblib").read_bytes())
    (folder / "kmeans.joblib").write_bytes(tree)
    _rehash(folder, "decision_tree.joblib")
    _rehash(folder, "kmeans.joblib")
    with pytest.raises(ArtifactIntegrityError, match="decision_tree.joblib contains KMeans, expected"):
        ModelService(folder)


def test_invalid_manifest_and_unsafe_names_give_clear_errors(tmp_path):
    folder = _copy(tmp_path)
    (folder / "MANIFEST.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError, match="not valid JSON"):
        ModelService(folder)
    (folder / "MANIFEST.json").write_text(json.dumps({"../config.py": "0" * 64}), encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError, match="unsafe file names"):
        ModelService(folder)
    with pytest.raises(ArtifactIntegrityError, match="artifact folder not found"):
        ModelService(tmp_path / "does-not-exist")


def test_models_are_not_modified_by_startup_or_predictions(app, andal_client):
    """Loading and predicting never refits: model parameters are identical to the Phase 1 files."""
    import io

    import joblib
    ml = app.extensions["civicpulse_ml"]
    andal_client.get(f"/estimates/asset/{asset_in(app, 'Andal', grade='C')}")
    fresh_lr = joblib.load(io.BytesIO((ARTIFACTS / "linear_regression.joblib").read_bytes()))
    fresh_km = joblib.load(io.BytesIO((ARTIFACTS / "kmeans.joblib").read_bytes()))
    assert np.array_equal(ml.lr.coef_, fresh_lr.coef_) and ml.lr.intercept_ == fresh_lr.intercept_
    assert np.array_equal(ml.km.cluster_centers_, fresh_km.cluster_centers_)
    for name in ("linear_regression.joblib", "decision_tree.joblib", "kmeans.joblib", "encoders.joblib"):
        manifest = json.loads((ARTIFACTS / "MANIFEST.json").read_text(encoding="utf-8"))
        assert hashlib.sha256((ARTIFACTS / name).read_bytes()).hexdigest() == manifest[name]


# ------------------------------------------------------------------ display helpers
def test_display_helpers():
    from datetime import datetime
    assert format_number(123456.78, 1) == "1,23,456.8" and format_number(4188) == "4,188"
    assert format_ist(datetime(2026, 11, 12, 19, 37)) == "13 Nov 2026, 01:07 IST"
    assert safe_http_url("https://example.org/x") and safe_http_url("javascript:alert(1)") is None


def test_dev_app_starts_without_env_file(monkeypatch):
    """Development needs no secret (random per run); production needs one (tested in test_security)."""
    monkeypatch.delenv("SECRET_KEY", raising=False)
    app = create_app("development", {"SECRET_KEY": None, "SQLALCHEMY_DATABASE_URI": "sqlite://"})
    assert len(app.config["SECRET_KEY"]) == 64 and app.debug is False
