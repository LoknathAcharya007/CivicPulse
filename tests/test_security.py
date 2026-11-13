"""CSRF, SQL injection, XSS, headers, private files, error handling, configuration safety."""
import pytest
from conftest import PASSWORDS, asset_in, csrf_from, login

from app import create_app
from app.blueprints.reports import safe_cell
from app.extensions import db
from app.models import CostEstimate, User

REAL_MANIFEST = "3352deb487605b3eda58d5af399419ef2ef1bd04b472db94eaec28d5cfed44de"


# ---------------------------------------------------------------- CSRF
def test_login_without_csrf_token_is_rejected(client):
    r = client.post("/login", data={"username": "off.andal", "password": PASSWORDS["off.andal"]})
    assert r.status_code == 400
    assert client.get("/").status_code == 302  # not logged in


def test_estimate_post_without_or_with_forged_token_is_rejected(app, andal_client):
    asset = asset_in(app, "Andal", not_grade="A")
    assert andal_client.post(f"/estimates/asset/{asset}", data={"target_grade": "A"}).status_code == 400
    assert andal_client.post(f"/estimates/asset/{asset}",
                             data={"target_grade": "A", "csrf_token": "forged.token.value"}).status_code == 400
    with app.app_context():
        assert CostEstimate.query.count() == 0


def test_csrf_token_from_another_session_is_rejected(app, client):
    other = app.test_client()
    stolen = csrf_from(other.get("/login").get_data(as_text=True))
    login(client, "off.andal")
    asset = asset_in(app, "Andal", not_grade="A")
    assert client.post(f"/estimates/asset/{asset}", data={"target_grade": "A", "csrf_token": stolen}).status_code == 400


# ---------------------------------------------------------------- SQL injection
@pytest.mark.parametrize("payload", ["' OR 1=1 --", "%' OR '1'='1", "x'; DROP TABLE assets; --", "%", "_"])
def test_sql_injection_in_search_is_harmless(app, andal_client, payload):
    r = andal_client.get("/assets/", query_string={"q": payload})
    assert r.status_code == 200
    assert "0 assets found" in r.get_data(as_text=True)
    with app.app_context():
        from app.models import Asset
        assert Asset.query.count() == 120  # table still there and complete


@pytest.mark.parametrize("query", [{"town": "1 OR 1=1"}, {"town": "-1"}, {"category": "Roads' OR '1'='1"},
                                   {"grade": "A' --"}, {"page": "1;DROP"}])
def test_sql_injection_in_filters_is_rejected(andal_client, query):
    assert andal_client.get("/assets/", query_string=query).status_code == 400


def test_sql_injection_in_login(client):
    r = login(client, "admin' --", "anything")
    assert r.status_code == 200 and "Invalid username or password." in r.get_data(as_text=True)
    assert login(client, "' OR '1'='1", "' OR '1'='1").status_code == 200
    assert client.get("/").status_code == 302


def test_sql_injection_in_path_ids(andal_client):
    for url in ["/assets/1%20OR%201=1", "/assets/1'", "/estimates/1;--", "/assets/-1"]:
        assert andal_client.get(url).status_code == 404


# ---------------------------------------------------------------- XSS
def test_xss_in_inspection_notes_is_escaped(app, andal_client):
    asset = asset_in(app, "Andal")
    token = csrf_from(andal_client.get(f"/assets/{asset}/inspections/new").get_data(as_text=True))
    payload = '<script>alert("x")</script><img src=x onerror=alert(1)>'
    r = andal_client.post(f"/assets/{asset}/inspections/new", data={
        "csrf_token": token, "assessed_on": "2026-11-01", "grade": "C", "damaged_share_pct": "30",
        "years_since_maintenance": "0", "complaints_last_12m": "2", "notes": payload})
    assert r.status_code == 302
    page = andal_client.get(f"/assets/{asset}").get_data(as_text=True)
    assert payload not in page
    assert "&lt;script&gt;alert(&#34;x&#34;)&lt;/script&gt;" in page


def test_xss_in_user_full_name_is_escaped(app, admin_client):
    page = admin_client.get("/admin/users/new").get_data(as_text=True)
    r = admin_client.post("/admin/users/new", data={
        "csrf_token": csrf_from(page), "username": "xss.user", "full_name": "<script>alert(1)</script>",
        "role": "admin", "password": "Strong-pass-2026", "confirm": "Strong-pass-2026"})
    assert r.status_code == 302
    page = admin_client.get("/admin/users").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_xss_in_search_box_is_escaped(andal_client):
    page = andal_client.get("/assets/", query_string={"q": '"><script>alert(1)</script>'}).get_data(as_text=True)
    assert "<script>alert(1)</script>" not in page


# ---------------------------------------------------------------- headers, files, errors, config
def test_security_headers_present(client):
    r = client.get("/login")
    h = r.headers
    csp = h["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert h["X-Content-Type-Options"] == "nosniff" and h["X-Frame-Options"] == "DENY"
    assert h["Referrer-Policy"] == "strict-origin-when-cross-origin" and h["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in h  # no CORS


@pytest.mark.parametrize("url", ["/ml_artifacts/MANIFEST.json", "/ml_artifacts/linear_regression.joblib",
                                 "/static/../ml_artifacts/MANIFEST.json", "/static/../../.env", "/.env",
                                 "/instance/civicpulse.db", "/config.py", "/static/../config.py",
                                 "/static/%2e%2e/%2e%2e/.env", "/artifacts/feature_spec.json"])
def test_private_files_are_not_served(client, url):
    assert client.get(url).status_code == 404


def test_unexpected_error_shows_no_details(app, andal_client, monkeypatch):
    import app.blueprints.dashboard as dashboard

    def boom(user):
        raise RuntimeError("secret internal detail /home/claude")
    monkeypatch.setattr(dashboard, "summary", boom)
    r = andal_client.get("/")
    body = r.get_data(as_text=True)
    assert r.status_code == 500 and "secret internal detail" not in body and "Traceback" not in body


def test_production_config_is_safe(monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    prod = create_app("production", {"SECRET_KEY": "p" * 64, "ARTIFACT_MANIFEST_SHA256": REAL_MANIFEST,
                                     "SQLALCHEMY_DATABASE_URI": "sqlite://"})
    assert prod.debug is False and prod.config["SESSION_COOKIE_SECURE"] is True
    with prod.app_context():
        db.create_all()
        from app.security import hash_password
        db.session.add(User(username="p.admin", full_name="P", role="admin",
                            password_hash=hash_password("Prod-pass-2026")))
        db.session.commit()
    c = prod.test_client()
    token = csrf_from(c.get("/login").get_data(as_text=True))
    r = c.post("/login", data={"username": "p.admin", "password": "Prod-pass-2026", "csrf_token": token},
               base_url="https://localhost")
    assert "Secure" in r.headers["Set-Cookie"] and "HttpOnly" in r.headers["Set-Cookie"]
    assert "Strict-Transport-Security" in r.headers


@pytest.mark.parametrize("overrides,message", [
    ({"SECRET_KEY": "short", "ARTIFACT_MANIFEST_SHA256": REAL_MANIFEST}, "SECRET_KEY"),
    ({"SECRET_KEY": None, "ARTIFACT_MANIFEST_SHA256": REAL_MANIFEST}, "SECRET_KEY"),
    ({"SECRET_KEY": "p" * 64, "ARTIFACT_MANIFEST_SHA256": ""}, "ARTIFACT_MANIFEST_SHA256"),
    ({"SECRET_KEY": "replace-with-64-hex-characters", "ARTIFACT_MANIFEST_SHA256": REAL_MANIFEST}, "placeholder"),
])
def test_production_refuses_unsafe_settings(overrides, message, monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    with pytest.raises(RuntimeError, match=message):
        create_app("production", {**overrides, "SQLALCHEMY_DATABASE_URI": "sqlite://"})


def test_csv_export_blocks_formula_injection():
    assert safe_cell("=HYPERLINK(\"http://x\")") == "'=HYPERLINK(\"http://x\")"
    assert safe_cell("+1") == "'+1" and safe_cell("@SUM(A1)") == "'@SUM(A1)" and safe_cell("-2") == "'-2"
    assert safe_cell("Roads") == "Roads" and safe_cell(None) == ""


def test_health_endpoint_reveals_nothing(client):
    assert client.get("/healthz").get_json() == {"status": "ok"}


@pytest.mark.parametrize("flag", ["1", "true", "on"])
def test_production_refuses_flask_debug_env(monkeypatch, flag):
    """Regression (bug found in verification): FLASK_DEBUG=1 used to enable the Werkzeug debugger in production."""
    monkeypatch.setenv("FLASK_DEBUG", flag)
    with pytest.raises(RuntimeError, match="Debug mode must be off in production"):
        create_app("production", {"SECRET_KEY": "p" * 64, "ARTIFACT_MANIFEST_SHA256": REAL_MANIFEST,
                                  "SQLALCHEMY_DATABASE_URI": "sqlite://"})


def test_debug_is_off_by_default_in_every_environment(monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    assert create_app("development").debug is False
    assert create_app("testing").debug is False
    monkeypatch.setenv("FLASK_DEBUG", "1")  # explicit opt-in works only in development
    assert create_app("development").debug is True
    assert create_app("testing").debug is False


def test_no_hardcoded_secret_key_in_source():
    from pathlib import Path as _P
    root = _P(__file__).resolve().parent.parent
    for path in [root / "config.py", *(root / "app").rglob("*.py")]:
        text = path.read_text(encoding="utf-8")
        assert "SECRET_KEY = \"" not in text and "SECRET_KEY='" not in text, path


def test_debug_setting_is_forced_off_in_production(monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    prod = create_app("production", {"SECRET_KEY": "p" * 64, "ARTIFACT_MANIFEST_SHA256": REAL_MANIFEST,
                                     "SQLALCHEMY_DATABASE_URI": "sqlite://", "DEBUG": True})
    assert prod.debug is False and prod.config["DEBUG"] is False
