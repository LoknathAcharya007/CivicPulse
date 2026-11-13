"""Security utilities: password hashing, role checks, town-level access control and HTTP security headers."""
from functools import wraps
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from flask import abort
from flask_login import current_user

from .extensions import db
from .models import Asset, CostEstimate, Town

# argon2-cffi's PasswordHasher uses Argon2id by default (memory-hard; salt generated per password).
_hasher = PasswordHasher()
# Used to spend the same time on unknown usernames, so response time does not reveal which usernames exist.
_DUMMY_HASH = _hasher.hash("not-a-real-password-used-for-timing-only")

MIN_PASSWORD_LENGTH = 10


def hash_password(password):
    return _hasher.hash(password)


def verify_password(stored_hash, password):
    """True/False; never raises for a wrong password or a malformed hash."""
    try:
        return _hasher.verify(stored_hash or _DUMMY_HASH, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def burn_verification_time(password):
    verify_password(_DUMMY_HASH, password)


def password_needs_rehash(stored_hash):
    return _hasher.check_needs_rehash(stored_hash)


def password_problems(password, username=""):
    """Simple, explainable policy. Returns a list of problems (empty list = acceptable)."""
    problems = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > 128:
        problems.append("at most 128 characters")
    if password.isdigit() or password.isalpha():
        problems.append("a mix of letters and numbers or symbols")
    if username and username.lower() in password.lower():
        problems.append("must not contain the username")
    return problems


# ---------------------------------------------------------------- authorization (always server side)
def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(401)
        if not current_user.is_admin:
            from .services.audit import log_event
            log_event("admin_area_denied", "denied", details=view.__name__)
            abort(403)
        return view(*args, **kwargs)
    return wrapper


def accessible_town_ids(user):
    """Administrators see every town; officials only the towns assigned to them."""
    if user.is_admin:
        return [t.id for t in db.session.query(Town.id).all()]
    return [t.id for t in user.towns]


def accessible_towns(user):
    query = Town.query.order_by(Town.name)
    if not user.is_admin:
        query = query.filter(Town.id.in_(accessible_town_ids(user)))
    return query.all()


def _deny(target_type, target_id):
    """Unknown and forbidden records both answer 404, so another town's record IDs cannot be discovered."""
    from .services.audit import log_event
    log_event("record_access_denied", "denied", target_type, target_id)
    abort(404)


MAX_ID = 2**31 - 1  # IDs outside this range cannot exist (and would overflow SQLite's INTEGER)


def _check_id(record_id):
    if not isinstance(record_id, int) or not 0 < record_id <= MAX_ID:
        abort(404)


def get_asset_or_404(asset_id):
    _check_id(asset_id)
    asset = db.session.get(Asset, asset_id)
    if asset is None:
        abort(404)
    if asset.town_id not in accessible_town_ids(current_user):
        _deny("asset", asset_id)
    return asset


def get_estimate_or_404(estimate_id):
    _check_id(estimate_id)
    estimate = db.session.get(CostEstimate, estimate_id)
    if estimate is None:
        abort(404)
    if estimate.asset.town_id not in accessible_town_ids(current_user):
        _deny("estimate", estimate_id)
    return estimate


def require_town_access(town_id):
    _check_id(town_id)
    if town_id not in accessible_town_ids(current_user):
        _deny("town", town_id)


# ---------------------------------------------------------------- redirects and headers
def is_safe_next_url(target):
    """Only allow redirects to a path on this site (prevents open redirects after login)."""
    if not target:
        return False
    parts = urlsplit(target)
    return not parts.scheme and not parts.netloc and target.startswith("/") and not target.startswith("//") \
        and "\\" not in target


CONTENT_SECURITY_POLICY = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                           "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


def apply_security_headers(response, https_only=False):
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    if https_only:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    # Pages show town records; do not let shared caches or the browser's back-cache keep them.
    if response.mimetype in ("text/html", "application/json", "text/csv"):
        response.headers["Cache-Control"] = "no-store"
    response.headers.pop("Server", None)
    return response
