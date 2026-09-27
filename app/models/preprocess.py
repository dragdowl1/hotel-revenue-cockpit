import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import RobustScaler, StandardScaler, QuantileTransformer


# percentile caps learned on the training matrix for the numeric columns with more than ten distinct values
def fit_caps(x, cols, low, high):
    caps = {}
    for col in cols:
        v = pd.to_numeric(x[col], errors="coerce")
        if v.nunique() > 10:
            caps[col] = (float(v.quantile(low)), float(v.quantile(high)))
    return caps


# apply learned caps, either clipping to the bounds or replacing values outside them by the training median
def apply_caps(x, caps, medians, mode):
    out = x.copy()
    for col, (lo, hi) in caps.items():
        if col not in out.columns:
            continue
        if mode == "clip":
            out[col] = out[col].clip(lo, hi)
        else:
            outside = (out[col] < lo) | (out[col] > hi)
            out[col] = out[col].where(~outside, medians.get(col, 0.0))
    return out


# fitted scaler for the numeric columns, one of standard, robust or quantile, none returns no scaler
def fit_scaler(x, cols, kind):
    if kind in (None, "none"):
        return None
    scaler = {"standard": StandardScaler(), "robust": RobustScaler(), "quantile": QuantileTransformer(n_quantiles=200, output_distribution="normal", random_state=7)}[kind]
    scaler.fit(x[cols].astype(float))
    return scaler


# apply a fitted scaler to the numeric columns, categoricals untouched
def apply_scaler(x, cols, scaler):
    if scaler is None:
        return x
    out = x.copy()
    out[cols] = scaler.transform(out[cols].astype(float))
    return out


# preprocessing state learned on a training matrix: caps, medians and scaler for the numeric columns of a recipe
def fit_preprocessing(x_train, num_cols, recipe):
    cap = recipe.get("cap")
    caps = fit_caps(x_train, num_cols, cap[0], cap[1]) if cap else {}
    medians = {c: float(x_train[c].median()) for c in caps}
    mode = recipe.get("cap_mode", "clip")
    scaler = fit_scaler(apply_caps(x_train, caps, medians, mode), num_cols, recipe.get("scaling", "none"))
    return {"caps": caps, "medians": medians, "cap_mode": mode, "scaler": scaler, "scaled_columns": num_cols if scaler is not None else []}


# apply learned preprocessing state to a matrix
def apply_preprocessing(x, state):
    out = apply_caps(x, state.get("caps") or {}, state.get("medians") or {}, state.get("cap_mode", "clip"))
    return apply_scaler(out, state.get("scaled_columns", []), state.get("scaler"))


# log odds of probabilities kept away from zero and one
def logit(p):
    q = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(q / (1 - q)).reshape(-1, 1)


# calibrator fitted on validation predictions with optional sample weights: isotonic with bounded output, platt scaling on the log odds, or seasonal isotonic with one curve per arrival month shrunk toward the global curve
def fit_calibrator(p_valid, y_valid, method, clip, weight=None, groups=None, min_rows=2000):
    if method == "platt":
        model = LogisticRegression(C=1e6, max_iter=1000)
        model.fit(logit(p_valid), y_valid, sample_weight=weight)
        return {"method": "platt", "model": model, "clip": list(clip)}
    model = IsotonicRegression(y_min=clip[0], y_max=clip[1], out_of_bounds="clip")
    model.fit(p_valid, y_valid, sample_weight=weight)
    if method != "seasonal":
        return {"method": "isotonic", "model": model, "clip": list(clip)}
    groups = np.asarray(groups)
    p_valid = np.asarray(p_valid, dtype=float)
    y_valid = np.asarray(y_valid, dtype=float)
    weight = np.ones(len(p_valid)) if weight is None else np.asarray(weight, dtype=float)
    seasons = {}
    for g in np.unique(groups):
        mask = groups == g
        if mask.sum() < min_rows:
            continue
        # the group curve is fit on the group rows plus the global rows at a lower weight, so thin months lean on the global curve
        p_fit = np.concatenate([p_valid[mask], p_valid])
        y_fit = np.concatenate([y_valid[mask], y_valid])
        w_fit = np.concatenate([weight[mask], weight * (min_rows / len(p_valid))])
        season = IsotonicRegression(y_min=clip[0], y_max=clip[1], out_of_bounds="clip")
        season.fit(p_fit, y_fit, sample_weight=w_fit)
        seasons[int(g)] = season
    return {"method": "seasonal", "model": model, "seasons": seasons, "clip": list(clip)}


# calibrated probability from raw scores, clipped to the calibrator bounds; the seasonal method needs the arrival month of every row
def calibrate(calibrator, raw, groups=None):
    raw = np.asarray(raw, dtype=float)
    if calibrator["method"] == "platt":
        p = calibrator["model"].predict_proba(logit(raw))[:, 1]
    else:
        p = calibrator["model"].predict(raw)
        if calibrator["method"] == "seasonal" and groups is not None:
            groups = np.asarray(groups)
            for g, season in calibrator["seasons"].items():
                mask = groups == g
                if mask.any():
                    p[mask] = season.predict(raw[mask])
    low, high = calibrator["clip"]
    return np.clip(p, low, high)
