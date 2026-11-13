"""Cost estimation engine - the centre of CivicPulse.

How an estimate is built (every amount is computed on the server):

  1. ML-predicted base cost      Phase 1 Linear Regression, CPWD DSR 2023 (Delhi) price level.
  2. Reference-rate adjustment   price-level factor; 1.00 (not applied) unless an official index is configured.
  3. Material                    included in the composite rates -> nothing added again.
  4. Labour                      included in the composite rates -> nothing added again.
  5. Transport (carriage)        included in the composite rates -> nothing added again.
  6. Contingency                 configurable percentage (a labelled assumption).
  7. Final estimated expenditure = 1 + 2 + 6.

The model learned from costs built with COMPOSITE rates (material + labour + tools/plant + carriage +
contractor overheads and profit in one rate). Adding material, labour or transport again would count
them twice, so those lines show 'Included' and add Rs 0. The labour wage rows are shown for reference only.

A reference-rate cross-check (package rate x quantity) is shown next to the ML figure but never added.
"""
from ..constants import GRADE_NAMES, GRADE_ORDER, UNIT_LABELS, grade_level, is_upgrade, upgrade_options
from ..models import CostReference


class CostingError(ValueError):
    """Bad request for an estimate (e.g. a downgrade) or inconsistent reference data."""


# Improvement packages: (reference id, quantity of that item per unit of the asset).
# Identical to the Phase 1 notebook; the result must equal feature_spec.json "package_unit_costs".
PACKAGES = {
    ("Roads", 1): ("Surface renewal: tack coat + 25 mm premix carpet", [("R01", 1.0), ("R03", 1.0)]),
    ("Roads", 2): ("Overlay: tack coat + 50 mm DBM + tack coat + 40 mm BC",
                   [("R01", 1.0), ("R04", 0.05), ("R01", 1.0), ("R05", 0.04)]),
    ("Roads", 3): ("Reconstruction: 200 mm GSB + 250 mm WMM + 60 mm DBM + 40 mm BC",
                   [("R06", 0.20), ("R07", 0.25), ("R02", 1.0), ("R04", 0.06), ("R01", 1.0), ("R05", 0.04)]),
    ("Drainage", 1): ("Desilting and minor repair", [("A01", 1.0)]),
    ("Drainage", 2): ("Relay with 300 mm RCC pipe + chamber every 30 m", [("R09", 1.0), ("R11", 1 / 30)]),
    ("Drainage", 3): ("Rebuild with 450 mm RCC pipe + manhole every 30 m", [("R10", 1.0), ("R12", 1 / 30)]),
    ("Streetlights", 1): ("LED luminaire replacement", [("A02", 1.0)]),
    ("Streetlights", 2): ("Luminaire + bracket + cable", [("A03", 1.0)]),
    ("Streetlights", 3): ("New pole, foundation, luminaire and cable", [("A04", 1.0)]),
    ("Water supply pipelines", 1): ("Leak repair + valve every 200 m", [("A05", 1.0), ("R16", 1 / 200)]),
    ("Water supply pipelines", 2): ("Replace 50% of pipe (150 mm DI) + valve and chamber every 250 m",
                                    [("R13", 0.5), ("R14", 1 / 250), ("R15", 1 / 250)]),
    ("Water supply pipelines", 3): ("Replace all pipe (150 mm DI) + valve and chamber every 250 m",
                                    [("R13", 1.0), ("R14", 1 / 250), ("R15", 1 / 250)]),
    ("Public parks", 1): ("Turf repair and pruning", [("A06", 1.0)]),
    ("Public parks", 2): ("Pave 15% (paths) + replanting", [("R17", 0.15), ("A07", 1.0)]),
    ("Public parks", 3): ("Pave 20% + kerb channel 2% + landscaping and amenities",
                          [("R18", 0.20), ("R19", 0.02), ("A08", 1.0)]),
}
# Grades gained -> package mix (same rule as the Phase 1 data generator).
STEP_MIX = {1: [(1, 1.0)], 2: [(2, 1.0)], 3: [(2, 0.5), (3, 0.5)], 4: [(3, 1.0)]}

LABOUR_REF_PREFIX = "L"


def load_references():
    return {r.ref_id: r for r in CostReference.query.all()}


def reference_info(ref):
    return {
        "ref_id": ref.ref_id, "item_code": ref.item_code, "description": ref.description, "unit": ref.unit,
        "rate_inr": ref.rate_inr, "rate_type": ref.rate_type, "verification_status": ref.verification_status,
        "source_document": ref.source_document, "source_url": ref.source_url,
        "document_date": ref.document_date, "price_basis": ref.effective_or_price_basis,
    }


def package_rate(category, level, refs):
    """Rate per asset unit for one package, plus the reference items it is built from."""
    name, items = PACKAGES[(category, level)]
    total, components = 0.0, []
    for ref_id, factor in items:
        ref = refs.get(ref_id)
        if ref is None or ref.rate_inr is None:
            raise CostingError(f"Reference rate {ref_id} is missing or unavailable")
        if not ref.used_in_cost_model:
            raise CostingError(f"Reference {ref_id} is not marked for use in the cost model")
        total += ref.rate_inr * factor
        components.append({**reference_info(ref), "factor_per_asset_unit": round(factor, 6),
                           "amount_per_asset_unit": round(ref.rate_inr * factor, 2)})
    return round(total, 2), name, components


def check_reference_consistency(refs, spec):
    """No double counting, and the database rates must give exactly the Phase 1 package costs."""
    problems = []
    for ref_id, ref in refs.items():
        if ref_id.startswith(LABOUR_REF_PREFIX) and ref.used_in_cost_model:
            problems.append(f"{ref_id}: labour wage rows must not be added to composite rates")
        if ref.rate_type == "Labour wage rate" and ref.used_in_cost_model:
            problems.append(f"{ref_id}: labour wage rate marked as used")
    for category, expected in spec["package_unit_costs"].items():
        for level, value in enumerate(expected, start=1):
            try:
                rate = package_rate(category, level, refs)[0]
            except CostingError as exc:
                problems.append(str(exc))
                continue
            if abs(rate - value) > 0.01:
                problems.append(f"{category} package {level}: {rate} in database, {value} in Phase 1")
    return problems


class CostEstimator:
    def __init__(self, ml_service, contingency_percent, price_index_factor=1.0, price_index_source=""):
        if not 0 <= contingency_percent <= 20:
            raise CostingError("CONTINGENCY_PERCENT must be between 0 and 20")
        if not 0.5 <= price_index_factor <= 2.0:
            raise CostingError("PRICE_INDEX_FACTOR must be between 0.5 and 2.0")
        if price_index_factor != 1.0 and not price_index_source.strip():
            raise CostingError("A PRICE_INDEX_FACTOR other than 1.0 needs PRICE_INDEX_SOURCE (official document)")
        self.ml = ml_service
        self.contingency_percent = float(contingency_percent)
        self.price_index_factor = float(price_index_factor)
        self.price_index_source = price_index_source.strip()

    def _reference_check(self, asset, current_grade, target_grade, refs):
        steps = grade_level(target_grade) - grade_level(current_grade)
        rate, names, components = 0.0, [], []
        for level, weight in STEP_MIX[steps]:
            r, name, comps = package_rate(asset.category, level, refs)
            rate += weight * r
            names.append(name if weight == 1.0 else f"{int(weight * 100)}% x {name}")
            components += comps
        return round(rate, 2), " + ".join(names), components

    def estimate(self, asset, assessment, target_grade, refs=None):
        """Full breakdown for one upgrade. Raises CostingError for anything that is not an upgrade."""
        if target_grade not in GRADE_ORDER:
            raise CostingError("Target grade must be one of A, B, C, D, E")
        current_grade = assessment.grade
        if not is_upgrade(current_grade, target_grade):
            raise CostingError(f"{current_grade} -> {target_grade} is not an improvement. "
                               "Only a better target grade can be estimated.")
        refs = refs if refs is not None else load_references()
        problems = check_reference_consistency(refs, self.ml.spec)
        if problems:
            raise CostingError("Cost reference data is inconsistent: " + "; ".join(problems[:3]))

        frame = self.ml.build_input(asset.town, asset, assessment, target_grade)
        ml_base = round(float(self.ml.predict_cost(frame)[0]), 2)
        priority = str(self.ml.predict_priority(frame.drop(columns=["target_grade"]))[0])
        group = int(self.ml.predict_group(frame.drop(columns=["target_grade"]))[0])

        adjustment = round(ml_base * (self.price_index_factor - 1.0), 2)
        adjusted = ml_base + adjustment
        contingency = round(adjusted * self.contingency_percent / 100, 2)
        final = round(adjusted + contingency, 2)

        ref_rate, package_name, components = self._reference_check(asset, current_grade, target_grade, refs)
        reference_cost = round(ref_rate * asset.quantity, 2)
        labour_refs = [reference_info(r) for k, r in sorted(refs.items()) if k.startswith(LABOUR_REF_PREFIX)]
        unit = UNIT_LABELS.get(asset.unit, asset.unit)
        composite_note = ("Included in the composite Schedule of Rates items used by the model "
                          "(material + labour + tools and plant + carriage + overheads and profit). "
                          "Not added again, to avoid double counting.")
        index_applied = self.price_index_factor != 1.0

        lines = [
            {"key": "ml_base", "label": "ML-predicted base cost", "amount": ml_base, "added": True,
             "status": "Model prediction",
             "basis": f"Phase 1 Linear Regression on log(cost) for {asset.quantity:,.1f} {unit}, "
                      f"{current_grade} ({GRADE_NAMES[current_grade]}) -> {target_grade} "
                      f"({GRADE_NAMES[target_grade]}). Price level: {self.ml.spec['price_basis']}."},
            {"key": "price_adjustment", "label": "Reference-rate (price level) adjustment", "amount": adjustment,
             "added": True, "status": "Applied" if index_applied else "Not applied - official index unavailable",
             "basis": (f"Factor {self.price_index_factor:.4f}. Source: {self.price_index_source}" if index_applied
                       else "Factor 1.0000. No official regional Building Cost Index for Paschim Bardhaman is "
                            "available to this project, so the DSR 2023 (Delhi) price level is kept unchanged.")},
            {"key": "material", "label": "Material cost", "amount": 0.0, "added": False,
             "status": "Included in composite rate", "basis": composite_note},
            {"key": "labour", "label": "Labour cost", "amount": 0.0, "added": False,
             "status": "Included in composite rate",
             "basis": composite_note + " WB minimum wage rows are listed for reference only; the "
                                       "1 Jul 2026 wage rate is unavailable.",
             "references": labour_refs},
            {"key": "transport", "label": "Transport (carriage of materials)", "amount": 0.0, "added": False,
             "status": "Included in composite rate",
             "basis": "DSR item rates are for finished work and include carriage within the normal lead. "
                      "Extra carriage beyond the normal lead is not estimated."},
            {"key": "contingency", "label": "Contingency", "amount": contingency, "added": True,
             "status": "Assumption (project setting)",
             "basis": f"{self.contingency_percent:g}% of the adjusted base cost. Set by CONTINGENCY_PERCENT; "
                      "not an official rate."},
            {"key": "final", "label": "Final estimated expenditure", "amount": final, "added": None,
             "status": "Total", "basis": "ML base cost + price-level adjustment + contingency."},
        ]
        return {
            "asset_id": asset.id, "assessment_id": assessment.id,
            "current_grade": current_grade, "target_grade": target_grade,
            "grades_gained": grade_level(target_grade) - grade_level(current_grade),
            "quantity": asset.quantity, "unit": asset.unit,
            "ml_base_cost": ml_base, "price_index_factor": self.price_index_factor,
            "price_adjustment": adjustment, "contingency_percent": self.contingency_percent,
            "contingency_amount": contingency, "final_cost": final,
            "reference_check": {
                "package": package_name, "rate_per_unit": ref_rate, "cost": reference_cost,
                "note": "Package rate x quantity from the reference table, before site factors and "
                        "mobilisation. Shown for comparison only; not added to the total.",
                "components": components,
            },
            "predicted_priority": priority, "predicted_group": group,
            "predicted_group_name": self.ml.group_name(group),
            "lines": lines,
            "input_snapshot": {k: (v.item() if hasattr(v, "item") else v)
                               for k, v in frame.iloc[0].to_dict().items()},
            "model_manifest_sha256": self.ml.manifest_sha256,
        }

    def compare(self, asset, assessment):
        """Every possible upgrade from the current grade (e.g. D -> C, D -> B, D -> A)."""
        refs = load_references()
        return [self.estimate(asset, assessment, target, refs) for target in upgrade_options(assessment.grade)]
