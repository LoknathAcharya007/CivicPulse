"""Creates the database tables and loads the demonstration data from the VERIFIED Phase 1 files.

All assets come from the Phase 1 synthetic asset register. They are illustrative records for a
demonstration - they are NOT real municipal assets - and every one is stored with is_illustrative=True.
"""
import io
from datetime import date

import pandas as pd

from .extensions import db
from .models import Assessment, Asset, CostReference, MaintenanceRecord, Town
from .services.costing import check_reference_consistency, load_references

SEED_NOTE = "Seeded from the Phase 1 synthetic asset register (illustrative; not a real inspection)."
MAINT_NOTE = ("Routine maintenance (illustrative). Date derived from 'years since maintenance' in the "
              "synthetic register; no cost recorded.")


def _none(value):
    return None if pd.isna(value) else value


def _years_before(day, years):
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 February in a non-leap year
        return day.replace(year=day.year - years, day=28)


def read_verified_csv(ml, name):
    """Read a CSV listed in MANIFEST.json, checking its SHA-256 first."""
    from .services.ml_service import ArtifactIntegrityError, sha256_bytes
    data = (ml.artifact_dir / name).read_bytes()
    if sha256_bytes(data) != ml.manifest.get(name):
        raise ArtifactIntegrityError(f"Checksum mismatch for {name}")
    return pd.read_csv(io.BytesIO(data))


def seed_database(ml):
    towns = read_verified_csv(ml, "towns.csv")
    refs = read_verified_csv(ml, "cost_references.csv")
    register = read_verified_csv(ml, "asset_register.csv")

    town_by_name = {}
    for row in towns.itertuples(index=False):
        town = Town(name=row.town, town_class=row.town_class, in_coalfield=row.in_coalfield == "Yes",
                    population_2011=int(row.population_2011), area_km2=float(row.area_km2),
                    district=row.district, latitude=float(row.lat), longitude=float(row.lon))
        db.session.add(town)
        town_by_name[row.town] = town

    for row in refs.itertuples(index=False):
        db.session.add(CostReference(
            ref_id=row.ref_id, category=row.category, item_code=_none(row.item_code),
            description=row.description, unit=_none(row.unit),
            rate_inr=None if pd.isna(row.rate_inr) else float(row.rate_inr), rate_type=row.rate_type,
            verification_status=row.verification_status, used_in_cost_model=row.used_in_cost_model == "Yes",
            source_document=_none(row.source_document), source_url=_none(row.source_url),
            document_date=_none(None if pd.isna(row.document_date) else str(row.document_date)),
            effective_or_price_basis=_none(row.effective_or_price_basis), notes=_none(row.notes)))
    db.session.flush()

    problems = check_reference_consistency(load_references(), ml.spec)
    if problems:
        raise RuntimeError("Cost references are inconsistent with Phase 1: " + "; ".join(problems))

    for row in register.itertuples(index=False):
        inspected = date.fromisoformat(row.last_inspection_date)
        age, ysm = int(row.asset_age_years), int(row.years_since_maintenance)
        town = town_by_name[row.town]
        asset = Asset(asset_code=row.asset_id, name=row.asset_name, town=town, category=row.asset_category,
                      unit=row.unit, quantity=float(row.quantity), commissioning_year=inspected.year - age,
                      population_served=int(row.population_served), current_grade=row.current_grade,
                      latitude=float(row.lat), longitude=float(row.lon), location_note=row.location_note,
                      is_illustrative=True)
        assessment = Assessment(asset=asset, assessed_on=inspected, grade=row.current_grade,
                                damaged_share_pct=float(row.damaged_share_pct), years_since_maintenance=ysm,
                                complaints_last_12m=int(row.complaints_last_12m), asset_age_years=age,
                                source="seed", notes=SEED_NOTE)
        result = ml.assess(town, asset, assessment)
        assessment.predicted_priority, assessment.predicted_group = result["priority"], result["group"]
        db.session.add_all([asset, assessment])
        if ysm < age:
            db.session.add(MaintenanceRecord(asset=asset, performed_on=_years_before(inspected, ysm),
                                             work_type="routine", description=MAINT_NOTE, source="seed"))
    db.session.commit()
    return {"towns": len(towns), "cost_references": len(refs), "assets": len(register)}
