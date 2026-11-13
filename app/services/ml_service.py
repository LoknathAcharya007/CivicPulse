"""Loads the three Phase 1 models and makes predictions with the SAME preprocessing code as the Colab notebook.

Safety rules followed here:
* Every artifact file is read once, its SHA-256 is checked against MANIFEST.json, and only those
  verified bytes are used (no second read that could be swapped in between).
* MANIFEST.json itself can be pinned with ARTIFACT_MANIFEST_SHA256 (required in production).
* joblib/pickle files are only ever loaded from this trusted folder - never from user uploads.
* Models are never trained or refitted by the app.
* At startup the 10 golden samples must be reproduced exactly, otherwise the app refuses to start.
"""
import hashlib
import io
import json
import types
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
import sklearn.exceptions

from .. import constants as C


class ArtifactIntegrityError(RuntimeError):
    """Raised when a model artifact is missing, modified, incompatible or gives different answers."""


MODEL_FILES = ("linear_regression.joblib", "decision_tree.joblib", "kmeans.joblib", "encoders.joblib")
REQUIRED_FILES = MODEL_FILES + ("preprocessing.py", "feature_spec.json", "golden_samples.csv")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


RESTORE_HINT = "Restore the original Phase 1 ml_artifacts folder from the project ZIP."


class ModelService:
    def __init__(self, artifact_dir, expected_manifest_sha256=""):
        try:
            self._load(artifact_dir, expected_manifest_sha256)
        except ArtifactIntegrityError as exc:
            raise ArtifactIntegrityError(f"CivicPulse cannot start - model artifacts failed verification: {exc}. "
                                         f"{RESTORE_HINT}") from None
        except Exception as exc:  # anything unexpected while loading is reported as an artifact problem
            raise ArtifactIntegrityError(f"CivicPulse cannot start - model artifacts could not be loaded "
                                         f"({type(exc).__name__}: {exc}). {RESTORE_HINT}") from exc

    def _load(self, artifact_dir, expected_manifest_sha256):
        self.artifact_dir = Path(artifact_dir)
        if not self.artifact_dir.is_dir():
            raise ArtifactIntegrityError(f"artifact folder not found: {self.artifact_dir}")
        self._verified = {}
        self._load_verified_files(expected_manifest_sha256)
        self.spec = json.loads(self._verified["feature_spec.json"].decode("utf-8"))
        self._check_versions()
        self.prep = self._load_preprocessing()
        self._check_feature_contract_before_load()
        with warnings.catch_warnings():
            # A model pickled by a different scikit-learn version must not load silently.
            warnings.simplefilter("error", category=getattr(sklearn.exceptions, "InconsistentVersionWarning",
                                                            UserWarning))
            loaded = []
            for name in MODEL_FILES:
                try:
                    loaded.append(joblib.load(io.BytesIO(self._verified[name])))
                except Exception as exc:
                    raise ArtifactIntegrityError(f"{name} could not be loaded ({type(exc).__name__})") from exc
            self.lr, self.dt, self.km, self.encoders = loaded
        self._check_model_contract()
        self.fill_values = self.spec["fill_values"]
        self.golden_result = self.check_golden_samples()

    # ------------------------------------------------------------------ loading and checks
    def _load_verified_files(self, expected_manifest_sha256):
        manifest_path = self.artifact_dir / "MANIFEST.json"
        if not manifest_path.is_file():
            raise ArtifactIntegrityError(f"MANIFEST.json not found in {self.artifact_dir}")
        manifest_bytes = manifest_path.read_bytes()
        self.manifest_sha256 = sha256_bytes(manifest_bytes)
        if expected_manifest_sha256 and self.manifest_sha256 != expected_manifest_sha256.strip().lower():
            raise ArtifactIntegrityError("MANIFEST.json does not match ARTIFACT_MANIFEST_SHA256")
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except ValueError as exc:
            raise ArtifactIntegrityError("MANIFEST.json is not valid JSON") from exc
        if not isinstance(manifest, dict):
            raise ArtifactIntegrityError("MANIFEST.json has an unexpected format")
        unsafe = [n for n in manifest if not isinstance(n, str) or Path(n).name != n or n.startswith(".")]
        if unsafe:  # only plain file names inside the artifact folder are allowed
            raise ArtifactIntegrityError(f"MANIFEST.json lists unsafe file names: {unsafe[:3]}")
        missing = [f for f in REQUIRED_FILES if f not in manifest]
        if missing:
            raise ArtifactIntegrityError(f"MANIFEST.json does not list: {missing}")
        for name in manifest:  # every listed file is checked, not only the ones the models need
            path = self.artifact_dir / name
            if not path.is_file():
                raise ArtifactIntegrityError(f"Missing artifact file: {name}")
            data = path.read_bytes()
            if sha256_bytes(data) != manifest[name]:
                raise ArtifactIntegrityError(f"Checksum mismatch for {name}")
            self._verified[name] = data
        self.manifest = manifest

    def _check_versions(self):
        trained = self.spec.get("versions", {})
        self.version_notes = []
        if trained.get("scikit-learn") != sklearn.__version__:
            raise ArtifactIntegrityError(
                f"scikit-learn {sklearn.__version__} installed, models were trained with "
                f"{trained.get('scikit-learn')}. Install the same version (see requirements.txt).")
        for name, module in (("numpy", np), ("pandas", pd), ("joblib", joblib)):
            if trained.get(name) != module.__version__:
                self.version_notes.append(f"{name} {module.__version__} (trained with {trained.get(name)})")

    def _load_preprocessing(self):
        """Build the module from the verified bytes of preprocessing.py (identical to the notebook's file)."""
        module = types.ModuleType("civicpulse_preprocessing")
        source = self._verified["preprocessing.py"].decode("utf-8")
        # The bytes were SHA-256 verified above, so this runs exactly the notebook's preprocessing.py
        # (the same as `import preprocessing`, without re-reading a file that could change in between).
        exec(compile(source, str(self.artifact_dir / "preprocessing.py"), "exec"), module.__dict__)  # noqa: S102  # nosec B102
        return module

    def _check_feature_contract_before_load(self):
        p = self.prep
        checks = {
            "grade order": (p.GRADE_ORDER, C.GRADE_ORDER), "categories": (p.CATEGORIES, C.CATEGORIES),
            "units": (p.UNITS, C.UNITS), "priority order": (p.PRIORITY_ORDER, C.PRIORITY_ORDER),
            "spec grade order": (self.spec["grade_order"], C.GRADE_ORDER),
        }
        bad = [k for k, (a, b) in checks.items() if a != b]
        if bad:
            raise ArtifactIntegrityError(f"preprocessing.py / feature_spec.json disagree with the app: {bad}")

    def _check_model_contract(self):
        from sklearn.cluster import KMeans
        from sklearn.linear_model import LinearRegression
        from sklearn.tree import DecisionTreeClassifier
        expected = {"linear_regression.joblib": (self.lr, LinearRegression),
                    "decision_tree.joblib": (self.dt, DecisionTreeClassifier),
                    "kmeans.joblib": (self.km, KMeans), "encoders.joblib": (self.encoders, dict)}
        for name, (obj, kind) in expected.items():
            if not isinstance(obj, kind):
                raise ArtifactIntegrityError(f"{name} contains {type(obj).__name__}, expected {kind.__name__}")
        if set(self.encoders) != {"grade", "coalfield", "town_class", "category"}:
            raise ArtifactIntegrityError("encoders.joblib does not contain the four Phase 1 encoders")
        if list(self.lr.feature_names_in_) != self.spec["lr_features"]:
            raise ArtifactIntegrityError("Linear Regression feature names differ from feature_spec.json")
        if list(self.dt.feature_names_in_) != self.spec["dt_features"]:
            raise ArtifactIntegrityError("Decision Tree feature names differ from feature_spec.json")
        if sorted(self.dt.classes_) != sorted(C.PRIORITY_ORDER):
            raise ArtifactIntegrityError("Decision Tree classes are not the five priority levels")
        if self.km.n_clusters != len(self.spec["group_names"]):
            raise ArtifactIntegrityError("K-Means cluster count differs from feature_spec.json")

    def check_golden_samples(self):
        golden = pd.read_csv(io.BytesIO(self._verified["golden_samples.csv"]))
        cost = self.predict_cost(golden).round(2)
        priority = self.predict_priority(golden)
        group = self.predict_group(golden)
        result = {
            "samples": len(golden),
            "cost_matches": bool(np.allclose(cost, golden["expected_cost_inr"], rtol=0, atol=0.01)),
            "priority_matches": bool((priority == golden["expected_priority"]).all()),
            "group_matches": bool((group == golden["expected_group"]).all()),
        }
        if not all(v for k, v in result.items() if k != "samples"):
            raise ArtifactIntegrityError(f"Golden samples not reproduced: {result}")
        return result

    # ------------------------------------------------------------------ predictions
    @staticmethod
    def build_input(town, asset, assessment, target_grade=None):
        """One row with exactly the raw columns the Phase 1 pipeline expects."""
        row = {
            "town_class": town.town_class,
            "in_coalfield": "Yes" if town.in_coalfield else "No",
            "asset_category": asset.category,
            "quantity": float(asset.quantity),
            "asset_age_years": float(assessment.asset_age_years),
            "years_since_maintenance": int(assessment.years_since_maintenance),
            "current_grade": assessment.grade,
            "damaged_share_pct": float(assessment.damaged_share_pct),
            "population_served": float(asset.population_served),
            "complaints_last_12m": float(assessment.complaints_last_12m),
        }
        if target_grade is not None:
            row["target_grade"] = target_grade
        return pd.DataFrame([row])

    def predict_cost(self, frame):
        """Linear Regression predicts log(cost); exp() converts back to rupees (DSR 2023 price level)."""
        return np.exp(self.lr.predict(self.prep.lr_matrix(frame, self.encoders, self.fill_values)))

    def predict_priority(self, frame):
        return self.dt.predict(self.prep.dt_matrix(frame, self.encoders, self.fill_values))

    def predict_group(self, frame):
        x = self.prep.km_matrix(frame, self.encoders, self.fill_values, self.spec["km_means"], self.spec["km_stds"])
        return self.km.predict(x)

    def group_name(self, group_id):
        return self.spec["group_names"].get(str(int(group_id)), f"Group {group_id}")

    def assess(self, town, asset, assessment):
        frame = self.build_input(town, asset, assessment)
        group = int(self.predict_group(frame)[0])
        return {"priority": str(self.predict_priority(frame)[0]), "group": group, "group_name": self.group_name(group)}

    # ------------------------------------------------------------------ analytics (read-only, from verified files)
    FEATURE_LABELS = {
        "log_quantity": "Quantity (log scale)", "current_level": "Current condition (E=0 ... A=4)",
        "target_level": "Target condition (E=0 ... A=4)", "damaged_share_pct": "Damaged share (%)",
        "asset_age_years": "Asset age (years)", "years_since_maintenance": "Years since maintenance",
        "complaints_last_12m": "Complaints (last 12 months)", "log_population_served": "Population served (log)",
        "coalfield_code": "Coalfield town", "town_class_code": "Municipal corporation (vs census town)",
        "asset_category_Drainage": "Category: Drainage (vs Roads)",
        "asset_category_Streetlights": "Category: Streetlights (vs Roads)",
        "asset_category_Water_supply_pipelines": "Category: Water supply pipelines (vs Roads)",
        "asset_category_Public_parks": "Category: Public parks (vs Roads)",
    }

    def analytics(self):
        """Everything the Analytics page shows, read from the verified Phase 1 files (cached).
        Nothing is retrained or recalculated: metrics, coefficients and profiles come from feature_spec.json,
        and the charts use the notebook's saved predictions (predictions_test_and_holdout.csv)."""
        if getattr(self, "_analytics", None) is not None:
            return self._analytics
        pred = pd.read_csv(io.BytesIO(self._verified["predictions_test_and_holdout.csv"]))
        clean = pd.read_csv(io.BytesIO(self._verified["assessment_records_clean.csv"]))
        raw_rows = sum(1 for _ in io.BytesIO(self._verified["assessment_records_raw.csv"])) - 1
        costed = pred.dropna(subset=["cost_inr", "predicted_cost_inr"])
        scatter = {split: [(round(a / 1e5, 3), round(p / 1e5, 3)) for a, p in
                           zip(part["cost_inr"], part["predicted_cost_inr"], strict=True)]
                   for split, part in costed.groupby("split")}
        order = C.PRIORITY_ORDER
        confusion = {}
        per_class = {}
        for split, part in pred.groupby("split"):
            table = pd.crosstab(part["priority"], part["predicted_priority"]).reindex(
                index=order, columns=order, fill_value=0)
            confusion[split] = table.values.tolist()
            rows = []
            for i, label in enumerate(order):
                tp = int(table.iloc[i, i])
                actual, predicted = int(table.iloc[i].sum()), int(table.iloc[:, i].sum())
                rows.append({"class": label, "support": actual,
                             "precision": round(tp / predicted, 3) if predicted else None,
                             "recall": round(tp / actual, 3) if actual else None})
            per_class[split] = rows
        coefficients = []
        for name, value in self.spec["lr_coefficients"].items():
            elasticity = name == "log_quantity"
            coefficients.append({"feature": name, "label": self.FEATURE_LABELS.get(name, name),
                                 "coefficient": value, "elasticity": elasticity,
                                 "effect_pct": None if elasticity else float(round((np.exp(value) - 1) * 100, 1))})
        importance = sorted(({"feature": k, "label": self.FEATURE_LABELS.get(k, k), "importance": v}
                             for k, v in self.spec["dt_feature_importance"].items()),
                            key=lambda r: r["importance"], reverse=True)
        self._analytics = {
            "metrics": self.spec["metrics"], "scatter": scatter, "confusion": confusion, "per_class": per_class,
            "coefficients": coefficients, "importance": importance,
            "inertias": self.spec["metrics"]["kmeans"]["inertias"],
            "k": self.spec["metrics"]["kmeans"]["k"], "profiles": self.spec["group_profiles"],
            "tree_depth": self.spec["decision_tree_depth"], "holdout_towns": self.spec["holdout_towns"],
            "seed": self.spec["random_seed"], "raw_rows": raw_rows,
            "splits": clean["split"].value_counts().to_dict(),
            "costed_counts": costed["split"].value_counts().to_dict(),
            "prediction_rows": len(pred),
        }
        return self._analytics

    def status(self):
        return {
            "manifest_sha256": self.manifest_sha256,
            "files_verified": len(self._verified),
            "golden": self.golden_result,
            "trained_versions": self.spec.get("versions", {}),
            "installed": {"scikit-learn": sklearn.__version__, "numpy": np.__version__,
                          "pandas": pd.__version__, "joblib": joblib.__version__},
            "version_notes": self.version_notes,
            "price_basis": self.spec.get("price_basis"),
            "metrics": self.spec.get("metrics", {}),
        }
