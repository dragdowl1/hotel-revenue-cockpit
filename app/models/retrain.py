import datetime
import json
import os
import numpy as np
import pandas as pd
import lightgbm as lgb
import joblib
from sklearn.metrics import roc_auc_score, brier_score_loss
from app import config
from app import db
from app.models import features
from app.models import registry
from app.models import score
from app.models import context_db
from app.models import preprocess
from app.replay import clock

rng = np.random.default_rng(11)
default_params = {"objective": "binary", "metric": ["binary_logloss", "auc"], "learning_rate": 0.03, "num_leaves": 63, "min_data_in_leaf": 100, "feature_fraction": 0.7, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 5.0, "verbose": -1, "num_threads": 2, "seed": 7}
# training recipe used when the champion carries none: window in years, feature list, caps, scaling, landmark samples, calibration and clip
default_recipe = {"window_years": None, "features": None, "cap": None, "cap_mode": "clip", "scaling": "none", "samples_per_booking": 2, "calibration": "isotonic", "clip": [0.005, 0.99], "class_weight": "none", "exposure_weight": False, "valid_days": 120, "holdout_days": 60, "params": default_params, "rounds": 4000, "patience": 150}


# expand bookings into as of observations at random dates between booking and departure from the books
def landmark(frame, k):
    parts = []
    for i in range(k):
        out = frame.copy()
        arrival = pd.to_datetime(out["arrival_date"])
        leave = pd.to_datetime(out["leave_books_date"])
        booked = pd.to_datetime(out["booking_date"])
        span = (leave.clip(upper=arrival) - booked).dt.days.clip(lower=0)
        offset = np.floor(rng.random(len(out)) * (span.values + 1))
        out["as_of"] = booked + pd.to_timedelta(offset, unit="D")
        out["exposure"] = span.values + 1
        parts.append(out)
    return pd.concat(parts, ignore_index=True)


# recipe of the champion artifact completed with defaults
def champion_recipe(champion):
    recipe = dict(default_recipe)
    if champion is not None:
        recipe.update(champion.get("recipe", {}))
        if "params" in champion and "params" not in champion.get("recipe", {}):
            recipe["params"] = champion["params"]
    params = dict(recipe["params"])
    params["num_threads"] = 2
    params["metric"] = ["binary_logloss", "auc"]
    recipe["params"] = params
    if recipe["features"] is None:
        recipe["features"] = features.feature_names()
    return recipe


# time based split of the known outcomes: arrival cohorts of the training window, then the validation cohort just before the cut
def split_cohorts(known, cut, recipe):
    arrival = pd.to_datetime(known["arrival_date"])
    train_end = cut - pd.Timedelta(days=recipe["valid_days"])
    train = known[arrival <= train_end]
    if recipe["window_years"] is not None:
        start = train_end - pd.DateOffset(years=recipe["window_years"])
        train = train[pd.to_datetime(train["arrival_date"]) > start]
    valid = known[(arrival > train_end) & (arrival <= cut)]
    return train, valid


# exposure weights of the recipe: days on the books so rows represent booking days, the population the calibrator must match
def exposure_weights(frame, recipe):
    if not recipe.get("exposure_weight", False):
        return np.ones(len(frame))
    return frame["exposure"].values / frame["exposure"].values.mean()


# sample weights of the learner: exposure times the class balance option, the balance never enters the calibrator
def sample_weights(frame, recipe):
    y = frame["is_canceled"].values.astype(int)
    weight = exposure_weights(frame, recipe)
    if recipe.get("class_weight") == "balanced":
        share = float(y.mean())
        weight = weight * np.where(y == 1, 0.5 / share, 0.5 / (1 - share))
    return weight


# train a challenger on the recipe: levels, caps and scaler learned on the training rows, early stopping and calibration on the validation cohort
def train_challenger(train_rows, valid_rows, recipe):
    k = recipe["samples_per_booking"]
    train = context_db.enrich(landmark(train_rows, k), replay=False)
    valid = context_db.enrich(landmark(valid_rows, k), replay=False)
    levels = features.fit_levels(train)
    cols = recipe["features"]
    num_cols = [c for c in cols if c not in features.categorical_columns]
    x_train = features.transform(train, levels)[cols]
    x_valid = features.transform(valid, levels)[cols]
    state = preprocess.fit_preprocessing(x_train, num_cols, recipe)
    x_train = preprocess.apply_preprocessing(x_train, state)
    x_valid = preprocess.apply_preprocessing(x_valid, state)
    y_train = train["is_canceled"].values.astype(int)
    y_valid = valid["is_canceled"].values.astype(int)
    w_train = sample_weights(train, recipe)
    w_valid = sample_weights(valid, recipe)
    d_train = lgb.Dataset(x_train, label=y_train, weight=w_train)
    d_valid = lgb.Dataset(x_valid, label=y_valid, weight=w_valid, reference=d_train)
    history = {}
    callbacks = [lgb.early_stopping(recipe["patience"], first_metric_only=True, verbose=False), lgb.record_evaluation(history)]
    model = lgb.train(recipe["params"], d_train, num_boost_round=recipe["rounds"], valid_sets=[d_train, d_valid], valid_names=["train", "validation"], callbacks=callbacks)
    raw_valid = model.predict(x_valid, num_iteration=model.best_iteration)
    calibrator = preprocess.fit_calibrator(raw_valid, y_valid, recipe["calibration"], recipe["clip"], exposure_weights(valid, recipe), pd.to_datetime(valid["arrival_date"]).dt.month.values)
    valid_auc = round(float(roc_auc_score(y_valid, raw_valid)), 4)
    return model, calibrator, levels, state, len(train), history, valid_auc


# auc and brier of an artifact on a frame observed at booking time, dataset time context
def evaluate(artifact, frame):
    p = score.predict(artifact, frame, replay=False)
    y = frame["is_canceled"].values.astype(int)
    return round(float(roc_auc_score(y, p)), 4), round(float(brier_score_loss(y, p)), 4)


# reliability table of an artifact on a frame, predicted against observed rate per quantile bin
def reliability(artifact, frame, bins=10):
    p = score.predict(artifact, frame, replay=False)
    y = frame["is_canceled"].values.astype(int)
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, bins - 1)
    rows = []
    for b in range(bins):
        mask = idx == b
        if mask.any():
            rows.append({"bin": b + 1, "rows": int(mask.sum()), "predicted": round(float(p[mask].mean()), 4), "observed": round(float(y[mask].mean()), 4)})
    return rows


# learning curve and gain based importance of a trained booster in the format of the notebook export
def curves_file(name, model, history, trained_at):
    curve = {}
    curve["iteration"] = list(range(1, len(history["train"]["binary_logloss"]) + 1))
    curve["train_loss"] = [round(float(v), 5) for v in history["train"]["binary_logloss"]]
    curve["test_loss"] = [round(float(v), 5) for v in history["validation"]["binary_logloss"]]
    curve["train_auc"] = [round(float(v), 5) for v in history["train"]["auc"]]
    curve["test_auc"] = [round(float(v), 5) for v in history["validation"]["auc"]]
    curve["best_iteration"] = int(model.best_iteration)
    gain = pd.Series(model.feature_importance("gain"), index=model.feature_name()).sort_values(ascending=False)
    share = gain / max(float(gain.sum()), 1e-9)
    rows = [{"feature": k, "gain": round(float(v), 1), "share": round(float(share[k]), 4)} for k, v in gain.items()]
    return {"version": name, "learning_curve": curve, "importance": rows, "trained_at": trained_at}


# train with the champion recipe on arrivals up to the cut, compare against the champion on the known arrivals after the cut and promote when better
def run():
    today = pd.Timestamp(clock.today())
    champion_name = registry.champion_name()
    champion = registry.load_champion()
    recipe = champion_recipe(champion)
    resolved = clock.resolved_before(today)
    known = resolved[resolved["outcome_known"].astype(bool)].copy()
    known["is_canceled"] = known["is_canceled"].astype(int)
    if len(known) < 5000:
        print("retrain skipped, known rows " + str(len(known)))
        return None
    cut = today - pd.Timedelta(days=recipe["holdout_days"])
    train_rows, valid_rows = split_cohorts(known, cut, recipe)
    holdout = known[pd.to_datetime(known["arrival_date"]) > cut].copy()
    holdout["as_of"] = pd.to_datetime(holdout["booking_date"])
    model, calibrator, levels, state, train_count, history, valid_auc = train_challenger(train_rows, valid_rows, recipe)
    name = registry.next_version()
    trained_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    challenger = {"model": model, "calibrator": calibrator, "levels": levels, "features": recipe["features"], "threshold": champion.get("threshold", 0.5) if champion is not None else 0.5, "context": True, "params": recipe["params"], "recipe": recipe, "samples_per_booking": recipe["samples_per_booking"], "best_iteration": int(model.best_iteration), "clip": tuple(recipe["clip"]), "family": "lightgbm", "version": name, "snapshot": str(cut.date()), "trained_at": trained_at}
    challenger.update(state)
    ch_auc, ch_brier = evaluate(challenger, holdout)
    cp_auc, cp_brier = (None, None)
    if champion is not None and holdout["is_canceled"].nunique() > 1:
        cp_auc, cp_brier = evaluate(champion, holdout)
    promoted = champion is None or (ch_brier <= cp_brier and ch_auc >= cp_auc - 0.005)
    os.makedirs(config.MODELS_DIR, exist_ok=True)
    joblib.dump(challenger, os.path.join(config.MODELS_DIR, name + ".joblib"))
    metrics = {"version": name, "snapshot": str(cut.date()), "holdout": {"rows": int(len(holdout)), "roc_auc": ch_auc, "brier": ch_brier}, "champion_holdout": {"roc_auc": cp_auc, "brier": cp_brier}, "validation": {"rows": int(len(valid_rows)), "roc_auc": valid_auc}, "train_rows": int(train_count), "train_bookings": int(len(train_rows)), "window_years": recipe["window_years"], "best_iteration": int(model.best_iteration), "promoted": bool(promoted), "trained_on_server": True, "feature_list": recipe["features"], "reliability_on_books": reliability(challenger, holdout), "reliability_label": "offline, known arrivals of the last 60 days scored at booking"}
    with open(os.path.join(config.MODELS_DIR, name + "_metrics.json"), "w") as f:
        json.dump(metrics, f, indent=2)
    with open(os.path.join(config.MODELS_DIR, name + "_curves.json"), "w") as f:
        json.dump(curves_file(name, model, history, trained_at), f)
    if promoted:
        registry.set_champion(name)
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    db.execute("insert into monitoring.retrain_log values (?, ?, ?, ?, ?, ?, ?, ?, ?)", [now, name, champion_name, ch_auc, cp_auc, ch_brier, cp_brier, promoted, int(train_count)])
    print("retrain " + name + " auc " + str(ch_auc) + " brier " + str(ch_brier) + ", champion auc " + str(cp_auc) + " brier " + str(cp_brier) + ", promoted " + str(promoted))
    return metrics
