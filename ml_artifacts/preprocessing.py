"""Shared preprocessing for CivicPulse - Infrastructure Cost Prediction.
Used by BOTH the Colab notebook and the Flask app so both transform data identically."""
import numpy as np
import pandas as pd

GRADE_ORDER = ["E", "D", "C", "B", "A"]                      # level 0 (worst) ... 4 (best)
CATEGORIES = ["Roads", "Drainage", "Streetlights", "Water supply pipelines", "Public parks"]
UNITS = {"Roads": "m2", "Drainage": "m", "Streetlights": "units",
         "Water supply pipelines": "m", "Public parks": "m2"}
PRIORITY_ORDER = ["Very Low", "Low", "Moderate", "High", "Critical"]

LR_BASE = ["log_quantity", "current_level", "target_level", "damaged_share_pct",
           "asset_age_years", "coalfield_code", "town_class_code"]
DT_BASE = ["current_level", "damaged_share_pct", "asset_age_years", "years_since_maintenance",
           "complaints_last_12m", "log_population_served"]
KM_FEATURES = ["current_level", "damaged_share_pct", "asset_age_years",
               "years_since_maintenance", "complaints_last_12m"]


def clean_text(df):
    """'  roads ' -> 'Roads',  ' c ' -> 'C'"""
    df = df.copy()
    lookup = {c.lower(): c for c in CATEGORIES}
    df["asset_category"] = df["asset_category"].astype(str).str.strip().str.lower().map(lookup)
    for col in ["current_grade", "target_grade"]:
        if col in df.columns:
            df[col] = df[col].where(df[col].isna(), df[col].astype(str).str.strip().str.upper())
    for col in ["in_coalfield", "town_class"]:
        df[col] = df[col].astype(str).str.strip().str.capitalize()
    return df


def is_upgrade(current_grade, target_grade):
    """True only if the target grade is strictly better than the current grade."""
    return GRADE_ORDER.index(target_grade) > GRADE_ORDER.index(current_grade)


def add_features(df, fill_values):
    """Fill missing numbers (training medians) and add engineered columns."""
    df = df.copy().fillna(fill_values)
    df["log_quantity"] = np.log(df["quantity"])
    df["log_population_served"] = np.log10(df["population_served"])
    return df


def encode(df, enc):
    """Ordinal (grades), label (yes/no, town class) and one-hot (category) encoding."""
    df = df.copy()
    df["current_level"] = enc["grade"].transform(df[["current_grade"]].to_numpy()).ravel()
    if "target_grade" in df.columns and df["target_grade"].notna().all():
        df["target_level"] = enc["grade"].transform(df[["target_grade"]].to_numpy()).ravel()
    df["coalfield_code"] = enc["coalfield"].transform(df["in_coalfield"])
    df["town_class_code"] = enc["town_class"].transform(df["town_class"])
    onehot = enc["category"].transform(df[["asset_category"]])
    names = [c.replace(" ", "_") for c in enc["category"].get_feature_names_out()]
    df[names] = onehot
    return df, names


def lr_matrix(df, enc, fill_values):
    df, names = encode(add_features(df, fill_values), enc)
    return df[LR_BASE + names]


def dt_matrix(df, enc, fill_values):
    df, names = encode(add_features(df, fill_values), enc)
    return df[DT_BASE + names]


def km_matrix(df, enc, fill_values, means, stds):
    """K-Means inputs scaled to z-scores with the TRAINING means and standard deviations."""
    df, _ = encode(add_features(df, fill_values), enc)
    x = df[KM_FEATURES].to_numpy(dtype=float)
    return (x - np.asarray(means)) / np.asarray(stds)
