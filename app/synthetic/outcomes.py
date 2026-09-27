import os
import joblib
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import roc_auc_score
from app import config

# the cancellation world model: cancel probability of a booking from its own attributes, fit on the real atoms in folds so that no synthetic booking is ever scored by a model that saw its atom's outcome
MODEL_PATH = os.path.join(config.MODELS_DIR, "synthetic_outcomes.joblib")
folds = 5
categorical = ["hotel", "meal", "country", "market_segment", "distribution_channel", "reserved_room_type", "deposit_type", "agent", "company", "customer_type"]
numeric = ["lead_time", "adr", "nights", "adults", "children", "babies", "is_repeated_guest", "previous_cancellations", "previous_bookings_not_canceled", "days_in_waiting_list", "required_car_parking_spaces", "total_of_special_requests", "arrival_month", "arrival_dow"]
params = {"objective": "binary", "learning_rate": 0.05, "num_leaves": 31, "min_data_in_leaf": 200, "feature_fraction": 0.7, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 10.0, "cat_smooth": 50.0, "cat_l2": 10.0, "min_data_per_group": 200, "verbose": -1, "num_threads": 2, "seed": 7}
rounds = 400


# fold of an atom from its real booking id
def fold_of(source_ids):
    return np.asarray(source_ids, dtype=np.int64) % folds


# category levels of the pool, the levels the fold models are trained with
def fit_levels(pool):
    return {col: sorted(pool[col].astype(str).unique().tolist()) for col in categorical}


# feature matrix for the world model, categoricals on the pool levels, unknown levels as missing
def matrix(frame, levels):
    x = pd.DataFrame(index=frame.index)
    for col in categorical:
        x[col] = pd.Categorical(frame[col].astype(str), categories=levels[col])
    for col in numeric:
        x[col] = pd.to_numeric(frame[col], errors="coerce").fillna(0).astype(float)
    return x


# fit one model per fold on the other folds of the pool, report the out of fold auc and save
def fit_world_model(pool):
    levels = fit_levels(pool)
    x = matrix(pool, levels)
    y = pool["is_canceled"].values.astype(int)
    fold = fold_of(pool["booking_id"].values)
    models = []
    oof = np.zeros(len(pool))
    for k in range(folds):
        train = fold != k
        booster = lgb.train(params, lgb.Dataset(x[train], label=y[train]), num_boost_round=rounds)
        models.append(booster)
        oof[~train] = booster.predict(x[~train])
    nonref = pool["deposit_type"].astype(str).values == "Non Refund"
    x_flex = x.copy()
    x_flex["deposit_type"] = pd.Categorical(np.where(nonref, "No Deposit", pool["deposit_type"].astype(str).values), categories=levels["deposit_type"])
    oof_flex = np.zeros(len(pool))
    for k in range(folds):
        mask = fold == k
        oof_flex[mask] = models[k].predict(x_flex[mask])
    flexible_rate = float(y[pool["deposit_type"].astype(str).values == "No Deposit"].mean())
    factor = prepaid_ratio * flexible_rate / max(float(oof_flex[nonref].mean()), 1e-6)
    out = {"models": models, "levels": levels, "oof_auc": round(float(roc_auc_score(y, oof)), 4), "atoms": int(len(pool)), "flexible_rate": round(flexible_rate, 4), "nonrefundable_factor": round(float(factor), 4)}
    os.makedirs(config.MODELS_DIR, exist_ok=True)
    joblib.dump(out, MODEL_PATH)
    return out


# the saved world model, fit from the pool when missing
def load_world_model(pool=None):
    if os.path.exists(MODEL_PATH):
        return joblib.load(MODEL_PATH)
    if pool is None:
        raise FileNotFoundError(MODEL_PATH)
    return fit_world_model(pool)


# non refundable bookings: the source data books unpaid group blocks and no shows as non refundable cancellations (99 percent cancel), while prepaid bookings cancel about half as often as the average booking in the industry; a non refundable booking therefore gets the probability it would have as a flexible booking, scaled so that the segment cancels at half the flexible rate
prepaid_ratio = 0.5


def cancel_probability(model, frame, source_ids):
    fold = fold_of(source_ids)
    x = matrix(frame, model["levels"])
    nonref = frame["deposit_type"].astype(str).values == "Non Refund"
    if nonref.any():
        flexible = pd.Categorical(np.where(nonref, "No Deposit", frame["deposit_type"].astype(str).values), categories=model["levels"]["deposit_type"])
        x["deposit_type"] = flexible
    p = np.zeros(len(frame))
    for k in range(folds):
        mask = fold == k
        if mask.any():
            p[mask] = model["models"][k].predict(x[mask])
    p[nonref] = np.clip(p[nonref] * model.get("nonrefundable_factor", prepaid_ratio), 0.0, 1.0)
    return p
