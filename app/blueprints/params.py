"""Strict parsing of query-string filters. Anything unexpected is rejected with 400, never passed to SQL."""
from flask import abort, request

from ..constants import CATEGORIES, GRADE_ORDER
from ..security import require_town_access


def int_arg(name, minimum=1):
    raw = request.args.get(name, "").strip()
    if not raw:
        return None
    if not raw.isdigit() or len(raw) > 9 or int(raw) < minimum:
        abort(400)
    return int(raw)


def text_arg(name, max_length=60):
    return request.args.get(name, "").strip()[:max_length] or None


def choice_arg(name, allowed, transform=None):
    raw = request.args.get(name, "").strip()
    if transform:
        raw = transform(raw)
    if not raw:
        return None
    if raw not in allowed:
        abort(400)
    return raw


def town_arg(name="town"):
    town_id = int_arg(name)
    if town_id is not None:
        require_town_access(town_id)  # another town's id -> 404 + audit entry
    return town_id


def asset_filters():
    return {"town_id": town_arg(), "category": choice_arg("category", CATEGORIES),
            "grade": choice_arg("grade", GRADE_ORDER, str.upper), "text": text_arg("q")}


def estimate_filters():
    return {"town_id": town_arg(), "category": choice_arg("category", CATEGORIES),
            "target": choice_arg("target", GRADE_ORDER, str.upper), "text": text_arg("q")}
