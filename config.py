"""Configuration. Every secret comes from environment variables (or a local .env file that is never committed)."""
import os
import secrets
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")  # does nothing if the file does not exist


class BaseConfig:
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL",
                                             f"sqlite:///{BASE_DIR / 'instance' / 'civicpulse.db'}")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Phase 1 artifacts: kept OUTSIDE the static folder, never served over HTTP.
    ARTIFACT_DIR = os.environ.get("ARTIFACT_DIR", str(BASE_DIR / "ml_artifacts"))
    # SHA-256 of MANIFEST.json. Pins the exact Phase 1 model set (required in production).
    ARTIFACT_MANIFEST_SHA256 = os.environ.get("ARTIFACT_MANIFEST_SHA256", "")

    # Sessions and cookies
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_NAME = "civicpulse_session"
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=int(os.environ.get("SESSION_MINUTES", "30")))
    REMEMBER_COOKIE_DURATION = timedelta(seconds=0)  # "remember me" is not offered

    # CSRF (Flask-WTF)
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600

    # Rate limiting (Flask-Limiter). memory:// is fine for one process; use Redis for several workers.
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_HEADERS_ENABLED = True
    LOGIN_RATE_LIMIT = "5 per minute"
    ESTIMATE_RATE_LIMIT = "30 per minute"

    MAX_CONTENT_LENGTH = 64 * 1024  # forms only; no file uploads

    # Costing settings (see app/services/costing.py). Both are labelled assumptions in every estimate.
    CONTINGENCY_PERCENT = float(os.environ.get("CONTINGENCY_PERCENT", "3"))
    PRICE_INDEX_FACTOR = float(os.environ.get("PRICE_INDEX_FACTOR", "1.0"))
    PRICE_INDEX_SOURCE = os.environ.get("PRICE_INDEX_SOURCE", "")

    ENV_NAME = "base"
    DEBUG = False
    TESTING = False


class DevelopmentConfig(BaseConfig):
    ENV_NAME = "development"
    DEBUG = False  # debug stays OFF unless you explicitly run with FLASK_DEBUG=1 (development only)
    SESSION_COOKIE_SECURE = False  # local http://127.0.0.1 only


class ProductionConfig(BaseConfig):
    ENV_NAME = "production"
    DEBUG = False


class TestingConfig(BaseConfig):
    ENV_NAME = "testing"
    TESTING = True
    SESSION_COOKIE_SECURE = False
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SECRET_KEY = secrets.token_hex(32)  # random per test run; no secret is written in the code
    RATELIMIT_ENABLED = True


CONFIGS = {"development": DevelopmentConfig, "production": ProductionConfig, "testing": TestingConfig}


def get_config(name=None):
    # Safe default: production (debug off, secure cookies, strict checks).
    return CONFIGS[(name or os.environ.get("CIVICPULSE_ENV", "production")).lower()]
