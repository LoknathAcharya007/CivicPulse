"""Database seeding and constraints, asset browsing/filters, inspections, improvements, invalid input and IDs."""
from datetime import date, timedelta

import pytest
from conftest import asset_in, csrf_from
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Assessment, Asset, AuditLog, CostReference, MaintenanceRecord, Town, User
from app.services.assets import ValidationError, record_improvement


def test_seed_counts_and_illustrative_labels(app):
    with app.app_context():
        assert Town.query.count() == 11 and CostReference.query.count() == 32
        assert Asset.query.count() == 120 and Assessment.query.count() == 120
        assert Asset.query.filter_by(is_illustrative=False).count() == 0
        assert all("synthetic" in a.location_note for a in Asset.query.all())
        assert Assessment.query.filter(Assessment.predicted_priority.is_(None)).count() == 0
        unavailable = CostReference.query.filter_by(ref_id="L05").one()
        assert unavailable.rate_inr is None and unavailable.used_in_cost_model is False


def test_foreign_keys_and_check_constraints_are_enforced(app):
    with app.app_context():
        assert db.session.execute(text("PRAGMA foreign_keys")).scalar() == 1
        town = Town.query.first()
        db.session.add(Asset(asset_code="BAD-UNIT", name="x", town=town, category="Roads", unit="m", quantity=10,
                             commissioning_year=2000, population_served=10, current_grade="C"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        db.session.add(Assessment(asset_id=999999, assessed_on=date(2026, 1, 1), grade="C", damaged_share_pct=1,
                                  years_since_maintenance=1, complaints_last_12m=1, asset_age_years=1, source="seed"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_database_rejects_downgrade_improvement(app):
    with app.app_context():
        asset = Asset.query.first()
        db.session.add(MaintenanceRecord(asset=asset, performed_on=date(2026, 1, 1), work_type="improvement",
                                         from_grade="B", to_grade="D", description="bad", source="user"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_filters_by_town_category_grade_and_text(app, andal_client):
    with app.app_context():
        andal = Town.query.filter_by(name="Andal").one().id
    page = andal_client.get("/assets/", query_string={"town": andal, "category": "Roads"}).get_data(as_text=True)
    assert "2 assets found" in page and "PB-AND-RO-01" in page
    page = andal_client.get("/assets/", query_string={"q": "PB-AND-DR"}).get_data(as_text=True)
    assert "2 assets found" in page
    with app.app_context():
        expected = Asset.query.filter(Asset.town_id.in_([andal]), Asset.current_grade == "C").count()
    page = andal_client.get("/assets/", query_string={"town": andal, "grade": "C"}).get_data(as_text=True)
    assert f"{expected} assets found" in page


@pytest.mark.parametrize("url", ["/assets/999999", "/assets/0", "/assets/abc", "/estimates/999999",
                                 "/estimates/asset/999999", "/assets/99999999999999999999",
                                 "/estimates/api/asset/999999/scenarios"])
def test_invalid_ids_return_404(andal_client, url):
    assert andal_client.get(url).status_code == 404


def test_asset_detail_shows_history(app, andal_client):
    asset = asset_in(app, "Andal")
    page = andal_client.get(f"/assets/{asset}").get_data(as_text=True)
    assert "Assessment history" in page and "Maintenance history" in page and "Illustrative synthetic record" in page


def _inspection(client, asset, **values):
    token = csrf_from(client.get(f"/assets/{asset}/inspections/new").get_data(as_text=True))
    data = {"csrf_token": token, "assessed_on": date.today().isoformat(), "grade": "D", "damaged_share_pct": "45",
            "years_since_maintenance": "2", "complaints_last_12m": "6", "notes": "Inspection test"}
    data.update(values)
    return client.post(f"/assets/{asset}/inspections/new", data=data)


def test_inspection_is_recorded_with_prediction(app, andal_client):
    asset = asset_in(app, "Andal", not_grade="D")
    assert _inspection(andal_client, asset).status_code == 302
    with app.app_context():
        a = db.session.get(Asset, asset)
        latest = a.latest_assessment()
        assert a.current_grade == "D" and latest.source == "inspection" and latest.predicted_priority
        assert latest.assessed_by.username == "off.andal" and a.assessments.count() == 2
        assert AuditLog.query.filter_by(action="assessment_recorded").count() == 1


@pytest.mark.parametrize("values", [
    {"damaged_share_pct": "150"}, {"damaged_share_pct": "-1"}, {"damaged_share_pct": "abc"},
    {"grade": "F"}, {"complaints_last_12m": "-3"}, {"years_since_maintenance": "x"},
    {"assessed_on": (date.today() + timedelta(days=3)).isoformat()}, {"assessed_on": "not-a-date"},
    {"assessed_on": "1990-01-01"}, {"years_since_maintenance": "90"}, {"notes": "x" * 501}])
def test_invalid_inspection_input_is_rejected(app, andal_client, values):
    asset = asset_in(app, "Andal")
    with app.app_context():
        before = Assessment.query.count()
    assert _inspection(andal_client, asset, **values).status_code == 400
    with app.app_context():
        assert Assessment.query.count() == before


def _improvement(client, asset, **values):
    token = csrf_from(client.get(f"/assets/{asset}/improvements/new").get_data(as_text=True))
    data = {"csrf_token": token, "performed_on": date.today().isoformat(), "to_grade": "A",
            "damaged_share_pct": "2", "description": "Resurfacing completed", "actual_cost_inr": "250000"}
    data.update(values)
    return client.post(f"/assets/{asset}/improvements/new", data=data)


def test_improvement_upgrade_is_recorded(app, andal_client):
    asset = asset_in(app, "Andal", grade="C")
    assert _improvement(andal_client, asset).status_code == 302
    with app.app_context():
        a = db.session.get(Asset, asset)
        record = a.maintenance.first()
        assert a.current_grade == "A" and record.work_type == "improvement"
        assert (record.from_grade, record.to_grade, record.actual_cost_inr) == ("C", "A", 250000)
        assert a.latest_assessment().source == "improvement"


@pytest.mark.parametrize("to_grade", ["C", "D", "E", "Q"])
def test_improvement_downgrade_rejected_by_form(app, andal_client, to_grade):
    asset = asset_in(app, "Andal", grade="C")
    assert _improvement(andal_client, asset, to_grade=to_grade).status_code == 400
    with app.app_context():
        assert db.session.get(Asset, asset).current_grade == "C"
        assert MaintenanceRecord.query.filter_by(work_type="improvement").count() == 0


def test_improvement_downgrade_rejected_by_service(app):
    """Defence in depth: the service rejects a downgrade even if a form check were bypassed."""
    with app.app_context():
        asset = db.session.get(Asset, asset_in(app, "Andal", grade="C"))
        user = User.query.filter_by(username="off.andal").one()
        with pytest.raises(ValidationError):
            record_improvement(asset, user, app.extensions["civicpulse_ml"], date.today(), "E", 50, "downgrade")
        assert asset.current_grade == "C"


def test_grade_a_asset_offers_no_improvement(app, andal_client):
    asset = asset_in(app, "Andal", grade="A")
    assert andal_client.get(f"/assets/{asset}/improvements/new").status_code == 302
    page = andal_client.get(f"/estimates/asset/{asset}").get_data(as_text=True)
    assert "A → " not in page  # no upgrade scenario exists above A
    r = andal_client.post(f"/estimates/asset/{asset}", data={"csrf_token": csrf_from(page), "target_grade": "A"})
    assert r.status_code == 400


def test_admin_creates_official_with_policy_checks(app, admin_client):
    with app.app_context():
        andal = Town.query.filter_by(name="Andal").one().id

    def create(**values):
        token = csrf_from(admin_client.get("/admin/users/new").get_data(as_text=True))
        data = {"csrf_token": token, "username": "new.official", "full_name": "New Official", "role": "official",
                "towns": [andal], "password": "Good-pass-2026", "confirm": "Good-pass-2026"}
        data.update(values)
        return admin_client.post("/admin/users/new", data=data)

    assert create(password="short", confirm="short").status_code == 400
    assert create(password="onlyletterslong", confirm="onlyletterslong").status_code == 400
    assert create(towns=[]).status_code == 400
    assert create(username="Bad Name!").status_code == 400
    assert create().status_code == 302
    assert create().status_code == 400  # duplicate username
    with app.app_context():
        user = User.query.filter_by(username="new.official").one()
        assert [t.name for t in user.towns] == ["Andal"] and user.password_hash.startswith("$argon2id$")
        assert AuditLog.query.filter_by(action="user_created").count() == 1


def test_admin_cannot_deactivate_self(app, admin_client):
    with app.app_context():
        admin_id = User.query.filter_by(username="admin").one().id
    token = csrf_from(admin_client.get(f"/admin/users/{admin_id}/edit").get_data(as_text=True))
    admin_client.post(f"/admin/users/{admin_id}/edit", data={"csrf_token": token, "full_name": "Test Admin",
                                                             "role": "admin"})
    with app.app_context():
        assert User.query.filter_by(username="admin").one().active is True


def test_reports_and_csv_export(app, admin_client):
    asset = asset_in(app, "Andal", grade="C")
    token = csrf_from(admin_client.get(f"/estimates/asset/{asset}").get_data(as_text=True))
    admin_client.post(f"/estimates/asset/{asset}", data={"csrf_token": token, "target_grade": "A"})
    report = admin_client.get("/reports/").get_data(as_text=True)
    assert "Andal" in report and "Durgapur" in report
    csv_text = admin_client.get("/reports/estimates.csv").get_data(as_text=True)
    lines = csv_text.strip().splitlines()
    assert len(lines) == 2 and lines[1].endswith("yes")
