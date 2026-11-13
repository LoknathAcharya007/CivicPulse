"""Role-based and town-based access control, including IDOR attempts on other towns' records."""
from conftest import asset_in, csrf_from, login

from app.extensions import db
from app.models import AuditLog, CostEstimate, Town, User


def test_official_cannot_open_admin_pages(app, andal_client):
    for url in ["/admin/users", "/admin/users/new", "/admin/audit", "/admin/cost-references", "/admin/system",
                "/admin/users/1/edit", "/admin/users/1/password"]:
        assert andal_client.get(url).status_code == 403, url
    with app.app_context():
        assert AuditLog.query.filter_by(action="admin_area_denied").count() >= 1


def test_official_cannot_post_to_admin(app, andal_client):
    token = csrf_from(andal_client.get("/").get_data(as_text=True))
    r = andal_client.post("/admin/users/new", data={"csrf_token": token, "username": "evil", "full_name": "Evil",
                                                    "role": "admin", "password": "Evil-pass-2026",
                                                    "confirm": "Evil-pass-2026"})
    assert r.status_code == 403
    with app.app_context():
        assert User.query.filter_by(username="evil").count() == 0


def test_admin_sees_every_town(app, admin_client):
    other = asset_in(app, "Durgapur")
    assert admin_client.get(f"/assets/{other}").status_code == 200
    assert "120 assets found" in admin_client.get("/assets/").get_data(as_text=True)


def test_official_list_shows_only_permitted_towns(andal_client):
    page = andal_client.get("/assets/").get_data(as_text=True)
    assert "20 assets found" in page  # Andal 10 + Ukhra 10
    assert "Asansol" not in page.split("assets found")[1].split("</table>")[0]


def test_cross_town_asset_access_returns_404(app, andal_client):
    other = asset_in(app, "Asansol", not_grade="A")
    for url in [f"/assets/{other}", f"/estimates/asset/{other}", f"/estimates/api/asset/{other}/scenarios",
                f"/assets/{other}/inspections/new", f"/assets/{other}/improvements/new"]:
        assert andal_client.get(url).status_code == 404, url
    with app.app_context():
        assert AuditLog.query.filter_by(action="record_access_denied").count() >= 5


def test_cross_town_writes_are_blocked(app, andal_client):
    other = asset_in(app, "Asansol", not_grade="A")
    token = csrf_from(andal_client.get("/").get_data(as_text=True))
    r = andal_client.post(f"/estimates/asset/{other}", data={"csrf_token": token, "target_grade": "A"})
    assert r.status_code == 404
    assert andal_client.post(f"/assets/{other}/inspections/new", data={
        "csrf_token": token, "assessed_on": "2026-10-01", "grade": "E", "damaged_share_pct": "90",
        "years_since_maintenance": "1", "complaints_last_12m": "3"}).status_code == 404
    with app.app_context():
        assert CostEstimate.query.count() == 0


def test_cross_town_filter_returns_404(app, andal_client):
    with app.app_context():
        asansol = Town.query.filter_by(name="Asansol").one().id
    assert andal_client.get(f"/assets/?town={asansol}").status_code == 404


def test_other_towns_saved_estimate_is_hidden(app, client):
    asset = asset_in(app, "Asansol", not_grade="A")
    login(client, "off.asansol")
    page = client.get(f"/estimates/asset/{asset}").get_data(as_text=True)
    with app.app_context():
        from app.models import Asset
        target = {"E": "D", "D": "C", "C": "B", "B": "A"}[db.session.get(Asset, asset).current_grade]
    r = client.post(f"/estimates/asset/{asset}", data={"csrf_token": csrf_from(page), "target_grade": target})
    estimate_url = r.headers["Location"]
    assert client.get(estimate_url).status_code == 200
    client.post("/logout", data={"csrf_token": csrf_from(client.get("/").get_data(as_text=True))})
    login(client, "off.andal")
    assert client.get(estimate_url).status_code == 404
    assert len(client.get("/reports/estimates.csv").get_data(as_text=True).strip().splitlines()) == 1  # header only
    assert client.get("/estimates/").get_data(as_text=True).count("No estimates saved yet") == 1


def test_anonymous_cannot_generate_estimates(app, client):
    asset = asset_in(app, "Andal", not_grade="A")
    token = csrf_from(client.get("/login").get_data(as_text=True))
    r = client.post(f"/estimates/asset/{asset}", data={"csrf_token": token, "target_grade": "A"})
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    with app.app_context():
        assert CostEstimate.query.count() == 0


def test_deactivated_user_session_is_rejected(app, andal_client):
    with app.app_context():
        User.query.filter_by(username="off.andal").one().active = False
        db.session.commit()
    assert andal_client.get("/").status_code == 302


def test_removing_a_town_takes_effect_after_admin_edit(app, client):
    login(client, "admin")
    with app.app_context():
        user = User.query.filter_by(username="off.andal").one()
        andal = Town.query.filter_by(name="Andal").one()
        user_id, andal_id = user.id, andal.id
    page = client.get(f"/admin/users/{user_id}/edit").get_data(as_text=True)
    r = client.post(f"/admin/users/{user_id}/edit", data={"csrf_token": csrf_from(page), "full_name": "Andal Official",
                                                          "role": "official", "towns": [andal_id], "active": "y"})
    assert r.status_code == 302
    with app.app_context():
        user = db.session.get(User, user_id)
        assert [t.name for t in user.towns] == ["Andal"] and user.session_version == 2
