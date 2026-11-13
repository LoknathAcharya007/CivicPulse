"""Business logic for assets, inspections, improvements and saved estimates.
Blueprints call these functions; they never write to the database themselves."""
from datetime import date

from sqlalchemy import func

from ..constants import CATEGORIES, GRADE_NAMES, GRADE_ORDER, PRIORITY_ORDER, is_upgrade
from ..extensions import db
from ..models import Assessment, Asset, CostEstimate, MaintenanceRecord, Town
from ..security import accessible_town_ids
from .audit import log_event

PAGE_SIZE = 25


class ValidationError(ValueError):
    pass


def escape_like(text):
    """Treat % and _ typed by the user as ordinary characters in a LIKE search."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_assets(user, town_id=None, category=None, grade=None, text=None, page=1):
    """Only ever searches inside the towns this user may access. Filter values must be pre-validated."""
    query = Asset.query.join(Town).filter(Asset.town_id.in_(accessible_town_ids(user)))
    if town_id is not None:
        query = query.filter(Asset.town_id == town_id)
    if category:
        query = query.filter(Asset.category == category)
    if grade:
        query = query.filter(Asset.current_grade == grade)
    if text:
        pattern = f"%{escape_like(text)}%"
        query = query.filter(db.or_(Asset.asset_code.ilike(pattern, escape="\\"),
                                    Asset.name.ilike(pattern, escape="\\")))
    query = query.order_by(Town.name, Asset.category, Asset.asset_code)
    return query.paginate(page=page, per_page=PAGE_SIZE, error_out=False)


def _check_date(on, asset):
    if on > date.today():
        raise ValidationError("The date cannot be in the future.")
    latest = asset.latest_assessment()
    if latest and on < latest.assessed_on:
        raise ValidationError(f"The date must be on or after the latest assessment ({latest.assessed_on}).")
    age = on.year - asset.commissioning_year
    if age < 0:
        raise ValidationError("The date is before the asset was commissioned.")
    return age


def record_inspection(asset, user, ml, assessed_on, grade, damaged_share_pct, years_since_maintenance,
                      complaints_last_12m, notes=""):
    """A new inspection records the OBSERVED condition. It may be lower than before (assets deteriorate);
    improvements are recorded separately and must always raise the grade."""
    if grade not in GRADE_ORDER:
        raise ValidationError("Invalid grade.")
    if not 0 <= damaged_share_pct <= 100:
        raise ValidationError("Damaged share must be between 0 and 100.")
    if complaints_last_12m < 0 or years_since_maintenance < 0:
        raise ValidationError("Counts cannot be negative.")
    age = _check_date(assessed_on, asset)
    if years_since_maintenance > age:
        raise ValidationError(f"Years since maintenance cannot exceed the asset age ({age} years).")
    assessment = Assessment(asset=asset, assessed_on=assessed_on, grade=grade, damaged_share_pct=damaged_share_pct,
                            years_since_maintenance=years_since_maintenance, complaints_last_12m=complaints_last_12m,
                            asset_age_years=age, source="inspection", notes=notes or None, assessed_by_id=user.id)
    result = ml.assess(asset.town, asset, assessment)
    assessment.predicted_priority, assessment.predicted_group = result["priority"], result["group"]
    previous = asset.current_grade
    asset.current_grade = grade
    db.session.add(assessment)
    db.session.commit()
    log_event("assessment_recorded", "success", "asset", asset.id, f"{previous} -> {grade} (inspection)", user=user)
    return assessment


def record_improvement(asset, user, ml, performed_on, to_grade, damaged_share_pct, description,
                       actual_cost_inr=None):
    """Completed improvement work. Downgrades are rejected here, in the form, and by a database CHECK."""
    from_grade = asset.current_grade
    if to_grade not in GRADE_ORDER or not is_upgrade(from_grade, to_grade):
        log_event("improvement_rejected", "failure", "asset", asset.id, f"{from_grade} -> {to_grade}", user=user)
        raise ValidationError(f"{from_grade} -> {to_grade} is not an improvement. Choose a better grade.")
    if not 0 <= damaged_share_pct <= 100:
        raise ValidationError("Damaged share must be between 0 and 100.")
    if actual_cost_inr is not None and not 0 <= actual_cost_inr <= 1e10:
        raise ValidationError("Actual cost must be between 0 and 10,00,00,00,000.")
    age = _check_date(performed_on, asset)
    latest = asset.latest_assessment()
    record = MaintenanceRecord(asset=asset, performed_on=performed_on, work_type="improvement",
                               from_grade=from_grade, to_grade=to_grade, actual_cost_inr=actual_cost_inr,
                               description=description, source="user", recorded_by_id=user.id)
    assessment = Assessment(asset=asset, assessed_on=performed_on, grade=to_grade,
                            damaged_share_pct=damaged_share_pct, years_since_maintenance=0,
                            complaints_last_12m=latest.complaints_last_12m if latest else 0,
                            asset_age_years=age, source="improvement",
                            notes=f"Post-improvement condition ({from_grade} -> {to_grade})", assessed_by_id=user.id)
    result = ml.assess(asset.town, asset, assessment)
    assessment.predicted_priority, assessment.predicted_group = result["priority"], result["group"]
    asset.current_grade = to_grade
    db.session.add_all([record, assessment])
    db.session.commit()
    log_event("improvement_recorded", "success", "asset", asset.id, f"{from_grade} -> {to_grade}", user=user)
    return record


def save_estimate(asset, user, estimator, target_grade):
    """Computes the estimate on the server and stores it with the user and a timestamp."""
    assessment = asset.latest_assessment()
    if assessment is None or assessment.grade != asset.current_grade:
        raise ValidationError("The asset's condition record is inconsistent; record a new inspection first.")
    result = estimator.estimate(asset, assessment, target_grade)
    estimate = CostEstimate(
        asset_id=asset.id, assessment_id=assessment.id, created_by_id=user.id,
        current_grade=result["current_grade"], target_grade=result["target_grade"],
        quantity=result["quantity"], unit=result["unit"], ml_base_cost=result["ml_base_cost"],
        price_index_factor=result["price_index_factor"], price_adjustment=result["price_adjustment"],
        contingency_percent=result["contingency_percent"], contingency_amount=result["contingency_amount"],
        final_cost=result["final_cost"], reference_rate_cost=result["reference_check"]["cost"],
        package_name=result["reference_check"]["package"], predicted_priority=result["predicted_priority"],
        predicted_group=result["predicted_group"], model_manifest_sha256=result["model_manifest_sha256"],
        input_snapshot=result["input_snapshot"],
        breakdown={"lines": result["lines"], "reference_check": result["reference_check"]},
    )
    db.session.add(estimate)
    db.session.commit()
    log_event("estimate_saved", "success", "estimate", estimate.id,
              f"asset {asset.asset_code} {estimate.current_grade}->{estimate.target_grade} "
              f"final {estimate.final_cost:.2f}", user=user)
    return estimate


def estimates_for_user(user, limit=None):
    query = (CostEstimate.query.join(Asset).filter(Asset.town_id.in_(accessible_town_ids(user)))
             .order_by(CostEstimate.created_at.desc(), CostEstimate.id.desc()))
    return query.limit(limit).all() if limit else query.all()


def summary(user):
    """Counts for the dashboard and reports, limited to the user's towns."""
    town_ids = accessible_town_ids(user)
    base = Asset.query.filter(Asset.town_id.in_(town_ids))
    by_grade = dict(db.session.query(Asset.current_grade, func.count()).filter(Asset.town_id.in_(town_ids))
                    .group_by(Asset.current_grade).all())
    by_category = dict(db.session.query(Asset.category, func.count()).filter(Asset.town_id.in_(town_ids))
                       .group_by(Asset.category).all())
    latest_priority = {}
    for asset in base.all():
        latest = asset.latest_assessment()
        if latest and latest.predicted_priority:
            latest_priority[latest.predicted_priority] = latest_priority.get(latest.predicted_priority, 0) + 1
    return {
        "total_assets": base.count(),
        "by_grade": [(g, GRADE_NAMES[g], by_grade.get(g, 0)) for g in reversed(GRADE_ORDER)],
        "by_category": [(c, by_category.get(c, 0)) for c in CATEGORIES],
        "by_priority": [(p, latest_priority.get(p, 0)) for p in reversed(PRIORITY_ORDER)],
    }


def town_report(user):
    """Per-town rows: asset counts, poor/very bad counts, high/critical priorities, latest estimates total."""
    rows = []
    for town in Town.query.filter(Town.id.in_(accessible_town_ids(user))).order_by(Town.name):
        assets = town.assets.all()
        latest_estimates, high = [], 0
        for asset in assets:
            latest = asset.latest_assessment()
            if latest and latest.predicted_priority in ("High", "Critical"):
                high += 1
            est = asset.estimates.first()
            if est:
                latest_estimates.append(est.final_cost)
        rows.append({"town": town, "assets": len(assets),
                     "poor_or_very_bad": sum(a.current_grade in ("D", "E") for a in assets),
                     "high_or_critical": high, "estimated_assets": len(latest_estimates),
                     "estimate_total": sum(latest_estimates)})
    return rows


# ---------------------------------------------------------------- read-only views used by the frontend
def latest_assessments(assets):
    """{asset_id: latest Assessment} for a list of assets (one query per asset; fine for this data size)."""
    return {a.id: a.latest_assessment() for a in assets}


def recent_assessments(user, limit=8):
    return (Assessment.query.join(Asset).filter(Asset.town_id.in_(accessible_town_ids(user)))
            .order_by(Assessment.created_at.desc(), Assessment.id.desc()).limit(limit).all())


def model_distribution(user):
    """How the Decision Tree and K-Means classified each asset at its latest assessment."""
    assets = Asset.query.filter(Asset.town_id.in_(accessible_town_ids(user))).all()
    priority, groups = {}, {}
    for latest in latest_assessments(assets).values():
        if latest is None:
            continue
        priority[latest.predicted_priority] = priority.get(latest.predicted_priority, 0) + 1
        groups[latest.predicted_group] = groups.get(latest.predicted_group, 0) + 1
    return priority, groups


def town_overview(user, text=None, town_class=None, coalfield=None):
    """One row per permitted town with real asset statistics. Filters are pre-validated by the caller."""
    query = Town.query.filter(Town.id.in_(accessible_town_ids(user)))
    if text:
        query = query.filter(Town.name.ilike(f"%{escape_like(text)}%", escape="\\"))
    if town_class:
        query = query.filter(Town.town_class == town_class)
    if coalfield is not None:
        query = query.filter(Town.in_coalfield == coalfield)
    rows = []
    for town in query.order_by(Town.name):
        assets = town.assets.all()
        latest = latest_assessments(assets)
        grades = {g: 0 for g in reversed(GRADE_ORDER)}
        categories = {c: 0 for c in CATEGORIES}
        for a in assets:
            grades[a.current_grade] += 1
            categories[a.category] += 1
        urgent = sum(1 for x in latest.values() if x and x.predicted_priority in ("High", "Critical"))
        estimates = [a.estimates.first() for a in assets]
        estimates = [e for e in estimates if e]
        rows.append({"town": town, "assets": len(assets), "grades": grades, "categories": categories,
                     "poor": grades["D"] + grades["E"], "urgent": urgent, "estimated_assets": len(estimates),
                     "estimate_total": sum(e.final_cost for e in estimates)})
    return rows


def filter_estimates(user, text=None, town_id=None, category=None, target=None):
    """Saved estimates in the user's towns, newest first. Filters are pre-validated by the caller."""
    query = CostEstimate.query.join(Asset).filter(Asset.town_id.in_(accessible_town_ids(user)))
    if town_id is not None:
        query = query.filter(Asset.town_id == town_id)
    if category:
        query = query.filter(Asset.category == category)
    if target:
        query = query.filter(CostEstimate.target_grade == target)
    if text:
        pattern = f"%{escape_like(text)}%"
        query = query.filter(db.or_(Asset.asset_code.ilike(pattern, escape="\\"),
                                    Asset.name.ilike(pattern, escape="\\")))
    return query.order_by(CostEstimate.created_at.desc(), CostEstimate.id.desc())
