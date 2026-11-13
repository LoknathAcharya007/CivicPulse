"""Cost estimation engine: upgrade paths, downgrade prevention, breakdown rules, manipulation attempts, INR format."""
import re

import pytest
from conftest import asset_in, csrf_from
from sqlalchemy.exc import IntegrityError

from app.constants import GRADE_ORDER, upgrade_options
from app.extensions import db
from app.formatting import format_inr
from app.models import Asset, CostEstimate, CostReference, User
from app.services.costing import CostEstimator, CostingError, check_reference_consistency, load_references


def _estimator(app):
    return app.extensions["civicpulse_estimator"]


def test_all_ten_upgrade_paths_are_supported_and_ordered(app):
    """E->D ... B->A: every path gives a positive estimate, and a better target always costs more."""
    with app.app_context():
        asset = db.session.get(Asset, asset_in(app, "Andal"))
        assessment = asset.latest_assessment()
        paths = 0
        for current in GRADE_ORDER[:-1]:
            assessment.grade = current  # in-memory only, to exercise every path on the same asset
            results = [_estimator(app).estimate(asset, assessment, t) for t in upgrade_options(current)]
            finals = [r["final_cost"] for r in results]
            assert all(f > 0 for f in finals) and finals == sorted(finals)
            paths += len(results)
        db.session.rollback()
        assert paths == 10


def test_breakdown_has_no_double_counting(app):
    with app.app_context():
        asset = db.session.get(Asset, asset_in(app, "Andal", not_grade="A"))
        target = upgrade_options(asset.current_grade)[-1]
        r = _estimator(app).estimate(asset, asset.latest_assessment(), target)
        lines = {line["key"]: line for line in r["lines"]}
        for key in ("material", "labour", "transport"):
            assert lines[key]["amount"] == 0 and lines[key]["added"] is False
            assert "Included" in lines[key]["status"]
        assert lines["price_adjustment"]["amount"] == 0 and "Not applied" in lines["price_adjustment"]["status"]
        assert lines["contingency"]["status"].startswith("Assumption")
        assert r["contingency_amount"] == pytest.approx(r["ml_base_cost"] * 0.03, abs=0.01)
        assert r["final_cost"] == pytest.approx(r["ml_base_cost"] + r["price_adjustment"] + r["contingency_amount"],
                                                abs=0.01)
        labour_refs = {x["ref_id"]: x for x in lines["labour"]["references"]}
        assert labour_refs["L05"]["rate_inr"] is None  # July 2026 wage: unavailable, not invented
        for comp in r["reference_check"]["components"]:  # source and date preserved for every rate used
            assert comp["source_document"] and comp["rate_type"] in ("Composite work rate", "Assumed composite rate")


def test_reference_rates_reproduce_phase1_package_costs(app):
    with app.app_context():
        assert check_reference_consistency(load_references(), app.extensions["civicpulse_ml"].spec) == []


def test_labour_marked_as_used_is_detected_as_double_counting(app):
    with app.app_context():
        CostReference.query.filter_by(ref_id="L01").one().used_in_cost_model = True
        db.session.commit()
        problems = check_reference_consistency(load_references(), app.extensions["civicpulse_ml"].spec)
        assert any("L01" in p for p in problems)
        asset = db.session.get(Asset, asset_in(app, "Andal", not_grade="A"))
        with pytest.raises(CostingError):
            _estimator(app).estimate(asset, asset.latest_assessment(), "A")


def test_price_index_needs_an_official_source(app):
    ml = app.extensions["civicpulse_ml"]
    with pytest.raises(CostingError):
        CostEstimator(ml, 3, price_index_factor=1.1, price_index_source="")
    with pytest.raises(CostingError):
        CostEstimator(ml, 50)
    assert CostEstimator(ml, 3, 1.1, "Example official index document").price_index_factor == 1.1


def test_service_rejects_downgrade_and_same_grade(app):
    with app.app_context():
        asset = db.session.get(Asset, asset_in(app, "Andal", grade="C"))
        for bad in ("C", "D", "E", "Z", "", None):
            with pytest.raises(CostingError):
                _estimator(app).estimate(asset, asset.latest_assessment(), bad)


def test_compare_page_offers_only_better_grades(app, andal_client):
    asset = asset_in(app, "Andal", grade="C")
    page = andal_client.get(f"/estimates/asset/{asset}").get_data(as_text=True)
    offered = re.findall(r'<input[^>]*name="target_grade"[^>]*value="([A-E])"', page)
    assert offered == ["B", "A"]  # only better grades, nearest first
    assert 'data-scenario="B"' in page and 'data-scenario="A"' in page and 'data-scenario="C"' not in page


@pytest.mark.parametrize("target", ["C", "D", "E", "Z", "<script>", "A' OR '1'='1"])
def test_post_downgrade_or_invalid_target_is_rejected(app, andal_client, target):
    asset = asset_in(app, "Andal", grade="C")
    token = csrf_from(andal_client.get(f"/estimates/asset/{asset}").get_data(as_text=True))
    r = andal_client.post(f"/estimates/asset/{asset}", data={"csrf_token": token, "target_grade": target})
    assert r.status_code == 400
    assert "<script>" not in r.get_data(as_text=True).split('id="main"')[1]
    with app.app_context():
        assert CostEstimate.query.count() == 0


def test_cost_manipulation_fields_are_ignored(app, andal_client):
    """Extra fields (amounts, quantity, grades, contingency) sent by a client have no effect."""
    asset = asset_in(app, "Andal", grade="C")
    token = csrf_from(andal_client.get(f"/estimates/asset/{asset}").get_data(as_text=True))
    r = andal_client.post(f"/estimates/asset/{asset}", data={
        "csrf_token": token, "target_grade": "A", "final_cost": "1", "ml_base_cost": "1", "quantity": "1",
        "contingency_percent": "0", "current_grade": "E", "price_index_factor": "0.01", "unit": "m"})
    assert r.status_code == 302
    with app.app_context():
        saved = CostEstimate.query.one()
        a = db.session.get(Asset, asset)
        expected = _estimator(app).estimate(a, a.latest_assessment(), "A")
        assert saved.final_cost == expected["final_cost"] > 1
        assert saved.quantity == a.quantity and saved.current_grade == "C" and saved.unit == a.unit
        assert saved.contingency_percent == 3.0 and saved.price_index_factor == 1.0


def test_saved_estimate_records_user_time_and_inputs(app, andal_client):
    asset = asset_in(app, "Andal", grade="C")
    token = csrf_from(andal_client.get(f"/estimates/asset/{asset}").get_data(as_text=True))
    location = andal_client.post(f"/estimates/asset/{asset}", data={"csrf_token": token, "target_grade": "B"}
                                 ).headers["Location"]
    page = andal_client.get(location).get_data(as_text=True)
    with app.app_context():
        e = CostEstimate.query.one()
        assert e.created_by.username == "off.andal" and e.created_at is not None
        assert e.input_snapshot["current_grade"] == "C" and e.input_snapshot["target_grade"] == "B"
        assert e.model_manifest_sha256 == app.extensions["civicpulse_ml"].manifest_sha256
        assert format_inr(e.final_cost) in page


def test_database_rejects_downgrade_estimate(app):
    with app.app_context():
        asset = db.session.get(Asset, asset_in(app, "Andal"))
        user = User.query.filter_by(username="admin").one()
        db.session.add(CostEstimate(
            asset_id=asset.id, assessment_id=asset.latest_assessment().id, created_by_id=user.id,
            current_grade="B", target_grade="D", quantity=1, unit=asset.unit, ml_base_cost=1, price_index_factor=1,
            price_adjustment=0, contingency_percent=3, contingency_amount=0, final_cost=1, reference_rate_cost=1,
            package_name="x", predicted_priority="Low", predicted_group=0, model_manifest_sha256="x" * 64,
            input_snapshot={}, breakdown={}))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_scenarios_api_matches_page_rules(app, andal_client):
    asset = asset_in(app, "Andal", grade="C")
    data = andal_client.get(f"/estimates/api/asset/{asset}/scenarios").get_json()
    assert [s["target_grade"] for s in data["scenarios"]] == ["B", "A"]
    assert data["asset"]["illustrative"] is True


@pytest.mark.parametrize("value,expected", [
    (0, "₹0"), (999, "₹999"), (1000, "₹1,000"), (100000, "₹1,00,000"), (1250000, "₹12,50,000"),
    (12345678.6, "₹1,23,45,679"), (-1250000, "-₹12,50,000"), (None, "Not available")])
def test_indian_currency_format(value, expected):
    assert format_inr(value) == expected


def test_indian_currency_format_with_paise():
    assert format_inr(304.15, paise=True) == "₹304.15"
    assert format_inr(11129.55, paise=True) == "₹11,129.55"
    assert format_inr(1234567.899, paise=True) == "₹12,34,567.90"
