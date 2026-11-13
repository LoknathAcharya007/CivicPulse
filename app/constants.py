"""Domain constants. They must match ml_artifacts/preprocessing.py - a test checks this."""

GRADE_ORDER = ["E", "D", "C", "B", "A"]  # worst -> best (same order as the Phase 1 OrdinalEncoder)
GRADE_NAMES = {"A": "Very Good", "B": "Good", "C": "Satisfactory", "D": "Poor", "E": "Very Bad"}

CATEGORIES = ["Roads", "Drainage", "Streetlights", "Water supply pipelines", "Public parks"]
UNITS = {"Roads": "m2", "Drainage": "m", "Streetlights": "units",
         "Water supply pipelines": "m", "Public parks": "m2"}
UNIT_LABELS = {"m2": "m²", "m": "metres", "units": "units"}

PRIORITY_ORDER = ["Very Low", "Low", "Moderate", "High", "Critical"]
TOWN_CLASSES = ["Municipal corporation", "Census town"]

ROLE_ADMIN = "admin"
ROLE_OFFICIAL = "official"
ROLES = {ROLE_ADMIN: "Administrator", ROLE_OFFICIAL: "Municipal Official"}


def grade_level(grade):
    """E=0 ... A=4. Raises ValueError for anything that is not a valid grade."""
    if grade not in GRADE_ORDER:
        raise ValueError(f"Invalid grade: {grade!r}")
    return GRADE_ORDER.index(grade)


def is_upgrade(current_grade, target_grade):
    """True only if the target is strictly better than the current grade."""
    return grade_level(target_grade) > grade_level(current_grade)


def upgrade_options(current_grade):
    """All grades better than the current one, nearest first (E -> D, C, B, A)."""
    return GRADE_ORDER[grade_level(current_grade) + 1:]
