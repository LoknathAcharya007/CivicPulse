"""Authentication: login, logout, password hashing, sessions, rate limiting, redirects."""
from conftest import PASSWORDS, csrf_from, login

from app.extensions import db
from app.models import AuditLog, User
from app.security import verify_password


def test_login_success_redirects_to_dashboard(client):
    r = login(client, "off.andal")
    assert r.status_code == 302 and r.headers["Location"] == "/"
    assert client.get("/").status_code == 200


def test_wrong_password_and_unknown_user_get_same_message(client, app):
    r1 = login(client, "off.andal", "wrong-password-1")
    r2 = login(client, "nobody", "wrong-password-1")
    assert r1.status_code == r2.status_code == 200
    assert "Invalid username or password." in r1.get_data(as_text=True)
    assert "Invalid username or password." in r2.get_data(as_text=True)
    with app.app_context():
        assert AuditLog.query.filter_by(action="login_failed").count() == 2


def test_passwords_are_stored_as_argon2id_hashes(app):
    with app.app_context():
        user = User.query.filter_by(username="off.andal").one()
        assert user.password_hash.startswith("$argon2id$")
        assert PASSWORDS["off.andal"] not in user.password_hash
        assert verify_password(user.password_hash, PASSWORDS["off.andal"])
        assert not verify_password(user.password_hash, "wrong")
        assert not verify_password("not-a-hash", "anything")


def test_inactive_account_cannot_log_in(client, app):
    with app.app_context():
        User.query.filter_by(username="off.andal").one().active = False
        db.session.commit()
    r = login(client, "off.andal")
    assert r.status_code == 200 and "Invalid username or password." in r.get_data(as_text=True)


def test_logout_requires_post_with_csrf(andal_client):
    assert andal_client.get("/logout").status_code == 405
    assert andal_client.post("/logout").status_code == 400  # no CSRF token
    token = csrf_from(andal_client.get("/").get_data(as_text=True))
    r = andal_client.post("/logout", data={"csrf_token": token})
    assert r.status_code == 302 and r.headers["Location"].startswith("/login")
    assert andal_client.get("/").status_code == 302  # logged out


def test_protected_pages_require_login(client):
    for url in ["/", "/assets/", "/assets/1", "/estimates/", "/estimates/asset/1", "/reports/",
                "/reports/estimates.csv", "/admin/users", "/admin/audit", "/admin/system"]:
        r = client.get(url)
        assert r.status_code == 302 and "/login" in r.headers["Location"], url
    assert client.get("/estimates/api/asset/1/scenarios").status_code == 401


def test_login_rate_limit_per_ip(client):
    codes = [login(client, "off.andal", f"wrong-{i}-password").status_code for i in range(6)]
    assert codes[:5] == [200] * 5
    assert codes[5] == 429


def test_open_redirect_after_login_is_blocked(client):
    token = csrf_from(client.get("/login").get_data(as_text=True))
    r = client.post("/login?next=https://evil.example.com/", data={
        "username": "off.andal", "password": PASSWORDS["off.andal"], "csrf_token": token})
    assert r.headers["Location"] == "/"


def test_safe_next_redirect_is_followed(client):
    token = csrf_from(client.get("/login").get_data(as_text=True))
    r = client.post("/login?next=/reports/", data={
        "username": "off.andal", "password": PASSWORDS["off.andal"], "csrf_token": token})
    assert r.headers["Location"] == "/reports/"


def test_password_reset_ends_existing_sessions(app, client):
    login(client, "off.andal")
    assert client.get("/").status_code == 200
    with app.app_context():
        User.query.filter_by(username="off.andal").one().session_version += 1
        db.session.commit()
    assert client.get("/").status_code == 302  # old session no longer accepted


def test_session_cookie_flags(client):
    r = login(client, "off.andal")
    cookie = r.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie


def test_audit_log_never_contains_passwords(app, client):
    login(client, "off.andal")
    login(app.test_client(), "off.asansol", "Typed-Wrong-Secret-1")
    with app.app_context():
        text = " ".join(f"{a.details} {a.target_id}" for a in AuditLog.query.all())
        assert PASSWORDS["off.andal"] not in text and "Typed-Wrong-Secret-1" not in text
