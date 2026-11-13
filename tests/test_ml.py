"""ML integration: same preprocessing as Colab, golden samples, full Phase 1 predictions, integrity failures."""
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app import constants as C
from app import create_app
from app.services.ml_service import ArtifactIntegrityError, ModelService

ARTIFACTS = Path(__file__).resolve().parent.parent / "ml_artifacts"
APP_DIR = Path(__file__).resolve().parent.parent / "app"


@pytest.fixture(scope="module")
def ml():
    return ModelService(ARTIFACTS)


def test_golden_samples_reproduced(ml):
    assert ml.golden_result == {"samples": 10, "cost_matches": True, "priority_matches": True, "group_matches": True}


def test_all_839_phase1_predictions_reproduced(ml):
    """The app's ModelService gives exactly the notebook's predictions for every test and held-out record."""
    clean = pd.read_csv(ARTIFACTS / "assessment_records_clean.csv")
    saved = pd.read_csv(ARTIFACTS / "predictions_test_and_holdout.csv").set_index("record_id")
    rows = clean[clean["split"] != "train"].set_index("record_id").loc[saved.index]
    costed = rows["target_grade"].notna()
    cost = np.exp(ml.lr.predict(ml.prep.lr_matrix(rows[costed], ml.encoders, ml.fill_values))).round(2)
    assert np.allclose(cost, saved.loc[costed, "predicted_cost_inr"], atol=0.01, rtol=0)
    assert (ml.predict_priority(rows) == saved["predicted_priority"]).all()
    assert (ml.predict_group(rows) == saved["predicted_group"]).all()
    assert len(saved) == 839


def test_preprocessing_is_the_phase1_file_byte_for_byte(ml):
    data = (ARTIFACTS / "preprocessing.py").read_bytes()
    manifest = json.loads((ARTIFACTS / "MANIFEST.json").read_text(encoding="utf-8"))
    assert hashlib.sha256(data).hexdigest() == manifest["preprocessing.py"]
    assert ml.prep.LR_BASE + ["asset_category_Drainage", "asset_category_Streetlights",
                              "asset_category_Water_supply_pipelines", "asset_category_Public_parks"] \
        == ml.spec["lr_features"]


def test_app_constants_match_preprocessing(ml):
    assert ml.prep.GRADE_ORDER == C.GRADE_ORDER and ml.prep.CATEGORIES == C.CATEGORIES
    assert ml.prep.UNITS == C.UNITS and ml.prep.PRIORITY_ORDER == C.PRIORITY_ORDER


def test_app_never_trains_models():
    source = "\n".join(p.read_text(encoding="utf-8") for p in APP_DIR.rglob("*.py"))
    assert ".fit(" not in source and "fit_transform" not in source and "partial_fit" not in source


def _copy(tmp_path):
    target = tmp_path / "artifacts"
    shutil.copytree(ARTIFACTS, target)
    return target


def test_modified_model_file_is_detected(tmp_path):
    folder = _copy(tmp_path)
    data = bytearray((folder / "decision_tree.joblib").read_bytes())
    data[-10] ^= 0xFF
    (folder / "decision_tree.joblib").write_bytes(bytes(data))
    with pytest.raises(ArtifactIntegrityError, match="Checksum mismatch for decision_tree.joblib"):
        ModelService(folder)


def test_modified_preprocessing_is_detected(tmp_path):
    folder = _copy(tmp_path)
    path = folder / "preprocessing.py"
    path.write_text(path.read_text(encoding="utf-8").replace("np.log(df[\"quantity\"])", "np.log1p(df[\"quantity\"])"),
                    encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError, match="preprocessing.py"):
        ModelService(folder)


def test_missing_file_is_detected(tmp_path):
    folder = _copy(tmp_path)
    (folder / "kmeans.joblib").unlink()
    with pytest.raises(ArtifactIntegrityError, match="kmeans.joblib"):
        ModelService(folder)


def test_pinned_manifest_detects_rewritten_manifest(tmp_path):
    """An attacker who changes a file AND rewrites MANIFEST.json is still caught by the pinned hash."""
    folder = _copy(tmp_path)
    pinned = hashlib.sha256((folder / "MANIFEST.json").read_bytes()).hexdigest()
    spec = json.loads((folder / "feature_spec.json").read_text(encoding="utf-8"))
    spec["fill_values"]["damaged_share_pct"] = 99.0
    (folder / "feature_spec.json").write_text(json.dumps(spec), encoding="utf-8")
    manifest = json.loads((folder / "MANIFEST.json").read_text(encoding="utf-8"))
    manifest["feature_spec.json"] = hashlib.sha256((folder / "feature_spec.json").read_bytes()).hexdigest()
    (folder / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError, match="ARTIFACT_MANIFEST_SHA256"):
        ModelService(folder, pinned)


def test_wrong_golden_answers_stop_startup(tmp_path):
    """Even with a consistent manifest, models that do not reproduce the golden samples are refused."""
    folder = _copy(tmp_path)
    golden = pd.read_csv(folder / "golden_samples.csv")
    golden.loc[0, "expected_cost_inr"] += 1000
    golden.to_csv(folder / "golden_samples.csv", index=False)
    manifest = json.loads((folder / "MANIFEST.json").read_text(encoding="utf-8"))
    manifest["golden_samples.csv"] = hashlib.sha256((folder / "golden_samples.csv").read_bytes()).hexdigest()
    (folder / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError, match="Golden samples not reproduced"):
        ModelService(folder)


def test_app_refuses_to_start_with_tampered_artifacts(tmp_path):
    folder = _copy(tmp_path)
    (folder / "linear_regression.joblib").write_bytes(b"not a model")
    with pytest.raises(ArtifactIntegrityError):
        create_app("testing", {"ARTIFACT_DIR": str(folder)})


def test_scikit_learn_version_mismatch_is_refused(tmp_path):
    folder = _copy(tmp_path)
    spec = json.loads((folder / "feature_spec.json").read_text(encoding="utf-8"))
    spec["versions"]["scikit-learn"] = "0.0.1"
    (folder / "feature_spec.json").write_text(json.dumps(spec), encoding="utf-8")
    manifest = json.loads((folder / "MANIFEST.json").read_text(encoding="utf-8"))
    manifest["feature_spec.json"] = hashlib.sha256((folder / "feature_spec.json").read_bytes()).hexdigest()
    (folder / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError, match="scikit-learn"):
        ModelService(folder)


def test_build_input_matches_notebook_columns(app):
    from app.extensions import db
    from app.models import Asset
    ml = app.extensions["civicpulse_ml"]
    with app.app_context():
        asset = db.session.get(Asset, 1)
        frame = ml.build_input(asset.town, asset, asset.latest_assessment(), "A")
        assert set(frame.columns) == {"town_class", "in_coalfield", "asset_category", "quantity", "asset_age_years",
                                      "years_since_maintenance", "current_grade", "damaged_share_pct",
                                      "population_served", "complaints_last_12m", "target_grade"}
        assert list(ml.prep.lr_matrix(frame, ml.encoders, ml.fill_values).columns) == ml.spec["lr_features"]
