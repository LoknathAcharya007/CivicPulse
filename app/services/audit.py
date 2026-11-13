"""Audit logging: one row per security-relevant action. Details are short text; never secrets."""
from flask import has_request_context, request
from flask_login import current_user

from ..extensions import db
from ..models import AuditLog


def log_event(action, outcome="success", target_type=None, target_id=None, details=None, user=None):
    if user is None and has_request_context() and current_user and current_user.is_authenticated:
        user = current_user
    entry = AuditLog(
        user_id=getattr(user, "id", None),
        username=getattr(user, "username", None),
        action=action[:50],
        outcome=outcome,
        target_type=target_type,
        target_id=None if target_id is None else str(target_id)[:40],
        ip_address=request.remote_addr if has_request_context() else None,
        details=None if details is None else str(details)[:500],
    )
    db.session.add(entry)
    db.session.commit()
    return entry
