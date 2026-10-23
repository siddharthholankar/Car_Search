"""Deal scoring.

    value_score = (predicted_market_price - listing_price) / predicted_market_price
    deal_score  = value_score + bonuses - penalties

The market price comes from a ridge regression on log(price) trained on the
listings collected so far. Each car's own prediction is made out-of-fold, so a
car's asking price never leaks into the estimate it's compared against.
"""

from __future__ import annotations

import datetime as dt
import logging

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import RidgeCV
from sklearn.model_selection import KFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from .config import ScoringConfig

log = logging.getLogger(__name__)

CATEGORICAL = ["make_model", "make_model_trim", "drivetrain", "state"]
NUMERIC = ["age", "age_sq", "miles_k", "miles_per_year_k", "days_on_market"]
OPTIONAL_COLUMNS = ["trim", "drivetrain", "state", "days_on_market", "accident_reported",
                    "salvage_or_rebuilt", "repossession", "rental_history", "owners",
                    "service_records", "certified"]


def build_features(df: pd.DataFrame, year: int | None = None) -> pd.DataFrame:
    year = year or dt.date.today().year
    out = pd.DataFrame(index=df.index)
    out["age"] = (year - df["year"]).clip(lower=0).astype(float)
    out["age_sq"] = out["age"] ** 2
    out["miles_k"] = df["miles"].astype(float) / 1000
    out["miles_per_year_k"] = out["miles_k"] / out["age"].clip(lower=0.5)
    dom = pd.to_numeric(df["days_on_market"], errors="coerce")
    out["days_on_market"] = dom.fillna(dom.median() if dom.notna().any() else 0)
    out["make_model"] = df["make"].str.lower() + " " + df["model"].str.lower()
    trim = df["trim"].fillna("unknown").astype(str).str.lower()
    out["make_model_trim"] = out["make_model"] + " " + trim
    out["drivetrain"] = df["drivetrain"].fillna("unknown").astype(str).str.upper()
    out["state"] = df["state"].fillna("unknown").astype(str).str.upper()
    return out


def make_model() -> Pipeline:
    pre = ColumnTransformer([
        ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=2), CATEGORICAL),
        ("num", StandardScaler(), NUMERIC),
    ])
    return Pipeline([("pre", pre), ("ridge", RidgeCV(alphas=np.logspace(-2, 2, 9)))])


def predict_market_prices(df: pd.DataFrame, min_training_rows: int = 30) -> tuple[pd.Series, dict]:
    """Predicted market price for every row of `df`, plus model diagnostics."""
    df = df.reset_index(drop=True)
    preds = pd.Series(np.nan, index=df.index)
    # Salvage/rebuilt cars are a different market; keep them out of the comps.
    train_mask = ~df["salvage_or_rebuilt"].fillna(0).astype(bool) & (df["price"] > 0)
    train = df[train_mask]

    if len(train) < min_training_rows:
        return _comps_median(df, train), {"method": "comps_median", "training_rows": len(train)}

    X, y = build_features(train), np.log(train["price"].astype(float))
    model = make_model()
    folds = KFold(n_splits=min(5, len(train)), shuffle=True, random_state=0)
    oof = np.exp(cross_val_predict(model, X, y, cv=folds))
    preds.loc[train.index] = oof
    model.fit(X, y)
    rest = df[~train_mask]
    if len(rest):
        preds.loc[rest.index] = np.exp(model.predict(build_features(rest)))

    errors = np.abs(oof - train["price"].to_numpy()) / train["price"].to_numpy()
    info = {
        "method": "ridge_log_price",
        "training_rows": len(train),
        "median_abs_pct_error": float(np.median(errors)),
        "alpha": float(model.named_steps["ridge"].alpha_),
    }
    return preds, info


def _comps_median(df: pd.DataFrame, train: pd.DataFrame) -> pd.Series:
    """Fallback for small datasets: median price of the same model within a year."""
    out = pd.Series(np.nan, index=df.index)
    for idx, row in df.iterrows():
        comps = train[(train["make"] == row["make"]) & (train["model"] == row["model"])
                      & ((train["year"] - row["year"]).abs() <= 1) & (train.index != idx)]
        if len(comps) >= 3:
            out[idx] = comps["price"].median()
    return out


def adjustments(row: pd.Series, cfg: ScoringConfig) -> tuple[float, list[str], bool]:
    """History penalties and bonuses for one listing: (total, notes, negotiable)."""
    p, b = cfg.penalties, cfg.bonuses
    total, notes = 0.0, []

    def add(amount: float, note: str) -> None:
        nonlocal total
        total += amount
        notes.append(f"{amount * 100:+.0f}% {note}")

    if _true(row.get("salvage_or_rebuilt")):
        add(-p["salvage_or_rebuilt"], "salvage/rebuilt title")
    if _true(row.get("accident_reported")):
        add(-p["accident"], "accident reported")
    if _true(row.get("repossession")):
        add(-p["repossession"], "repossession")
    if _true(row.get("rental_history")):
        add(-p["rental"], "rental history")
    owners = row.get("owners")
    if owners is not None and not pd.isna(owners):
        if owners > 3:
            add(-p["many_owners"], f"{int(owners)} owners")
        elif owners == 1:
            add(b["one_owner"], "one owner")
    if _true(row.get("service_records")):
        add(b["service_records"], "service records")
    if _true(row.get("certified")):
        add(b["certified"], "certified pre-owned")
    dom = row.get("days_on_market")
    negotiable = dom is not None and not pd.isna(dom) and dom > cfg.negotiable_days
    if negotiable:
        add(b["long_on_market"], f"{int(dom)} days listed - room to negotiate")
    return total, notes, negotiable


def score_listings(df: pd.DataFrame, cfg: ScoringConfig) -> tuple[pd.DataFrame, dict]:
    """Score every listing in `df`. Returns (vin, market_price, value_score, deal_score, score_notes, negotiable)."""
    if df.empty:
        return pd.DataFrame(columns=["vin", "market_price", "value_score", "deal_score",
                                     "score_notes", "negotiable"]), {"method": "none", "training_rows": 0}
    df = df.reset_index(drop=True).reindex(columns=[*df.columns, *(c for c in OPTIONAL_COLUMNS
                                                                    if c not in df.columns)])
    market, info = predict_market_prices(df, cfg.min_training_rows)
    value = (market - df["price"]) / market
    adj = [adjustments(row, cfg) for _, row in df.iterrows()]
    out = pd.DataFrame({
        "vin": df["vin"],
        "market_price": market.round(0),
        "value_score": value,
        "deal_score": value + pd.Series([a[0] for a in adj]),
        "score_notes": [a[1] for a in adj],
        "negotiable": [a[2] for a in adj],
    })
    log.info("scored %d listings with %s", len(out), info)
    return out, info


def _true(value) -> bool:
    return value is not None and not pd.isna(value) and bool(value)
