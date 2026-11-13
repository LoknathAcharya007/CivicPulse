"""Shared fixtures: a fresh in-memory database seeded from the verified Phase 1 files for every test.
CSRF protection stays ON in tests, so every form test submits a real token."""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app  # noqa: E402
from app.extensions import db, limiter  # noqa: E402
from app.models import Asset, Town, User  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.seed import seed_database  # noqa: E402

PASSWORDS = {"admin": "Admin-pass-2026", "off.andal": "Andal-pass-2026", "off.asansol": "Asansol-pass-2026"}


@pytest.fixture
def app():
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        seed_database(app.extensions["civicpulse_ml"])
        towns = {t.name: t for t in Town.query.all()}
        db.session.add_all([
            User(username="admin", full_name="Test Admin", role="admin",
                 password_hash=hash_password(PASSWORDS["admin"])),
            User(username="off.andal", full_name="Andal Official", role="official",
                 password_hash=hash_password(PASSWORDS["off.andal"]), towns=[towns["Andal"], towns["Ukhra"]]),
            User(username="off.asansol", full_name="Asansol Official", role="official",
                 password_hash=hash_password(PASSWORDS["off.asansol"]), towns=[towns["Asansol"]]),
        ])
        db.session.commit()
        limiter.reset()
    # No app context stays open during the test: each request gets its own, exactly as in production.
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def csrf_from(html):
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    assert match, "no CSRF token in page"
    return match.group(1)


def login(client, username, password=None):
    token = csrf_from(client.get("/login").get_data(as_text=True))
    return client.post("/login", data={"username": username, "password": password or PASSWORDS[username],
                                       "csrf_token": token})


def asset_in(app, town_name, grade=None, not_grade=None):
    with app.app_context():
        query = Asset.query.join(Town).filter(Town.name == town_name)
        if grade:
            query = query.filter(Asset.current_grade == grade)
        if not_grade:
            query = query.filter(Asset.current_grade != not_grade)
        return query.order_by(Asset.id).first().id


@pytest.fixture
def andal_client(client):
    assert login(client, "off.andal").status_code == 302
    return client


@pytest.fixture
def admin_client(client):
    assert login(client, "admin").status_code == 302
    return client
