import datetime
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, brier_score_loss
from app import db
from app.models import features
from app.models import registry
from app.models import context_db
from app.replay import clock

psi_bins = 10
warn_psi = 0.1
alert_psi = 0.25


# create the monitoring tables if missing
def ensure_tables():
    db.execute("create schema if not exists monitoring")
    db.execute("create table if not exists monitoring.drift (run_at timestamp, feature varchar, psi double, status varchar, reference_rows integer, current_rows integer)")
    db.execute("create table if not exists monitoring.performance (run_at timestamp, window_start date, window_end date, rows integer, cancel_rate double, roc_auc double, brier double, brier_base double, model_version varchar)")
    db.execute("create table if not exists monitoring.retrain_log (run_at timestamp, challenger varchar, champion varchar, challenger_auc double, champion_auc double, challenger_brier double, champion_brier double, promoted boolean, train_rows integer)")


# population stability index between two numeric series on equal width bins over the pooled 1st to 99th percentile range; series with few distinct values are compared level by level
def psi_numeric(reference, current):
    reference = np.asarray(reference, dtype=float)
    current = np.asarray(current, dtype=float)
    pooled = np.concatenate([reference, current])
    if len(np.unique(pooled)) <= 20:
        return psi_categorical(pd.Series(reference).round(6).astype(str), pd.Series(current).round(6).astype(str))
    low, high = np.quantile(pooled, [0.01, 0.99])
    if high <= low:
        return 0.0
    inner = np.linspace(low, high, psi_bins + 1)[1:-1]
    ref_counts = np.bincount(np.digitize(reference, inner), minlength=psi_bins) / max(len(reference), 1)
    cur_counts = np.bincount(np.digitize(current, inner), minlength=psi_bins) / max(len(current), 1)
    ref_counts = np.clip(ref_counts, 1e-4, None)
    cur_counts = np.clip(cur_counts, 1e-4, None)
    return float(np.sum((cur_counts - ref_counts) * np.log(cur_counts / ref_counts)))


# population stability index between two categorical series
def psi_categorical(reference, current):
    ref_share = reference.value_counts(normalize=True)
    cur_share = current.value_counts(normalize=True)
    levels = ref_share.index.union(cur_share.index)
    r = np.clip(ref_share.reindex(levels).fillna(0).values, 1e-4, None)
    c = np.clip(cur_share.reindex(levels).fillna(0).values, 1e-4, None)
    return float(np.sum((c - r) * np.log(c / r)))


# label a psi value
def psi_status(value):
    if value >= alert_psi:
        return "alert"
    if value >= warn_psi:
        return "warn"
    return "ok"


# running volume counts and shares of the account history move with the stream by construction, their entities are watched through the agent, country and company levels instead
volume_columns = ("agent_prior_n", "agent_prior_365", "country_prior_365", "country_share_365", "hotel_prior_365", "company_prior_n")


# compare the last 30 replay days of new bookings with the same window one year earlier, only the features the champion uses
def run_drift():
    artifact = registry.load_champion()
    if artifact is None:
        return 0
    today = pd.Timestamp(clock.today())
    current = clock.created_between(today - pd.Timedelta(days=30), today)
    reference = clock.created_between(today - pd.Timedelta(days=364 + 30), today - pd.Timedelta(days=364))
    if len(current) < 100 or len(reference) < 100:
        return 0
    if artifact.get("context", False):
        reference = context_db.enrich(reference, replay=True)
        current = context_db.enrich(current, replay=True)
    x_ref = features.transform(reference, artifact["levels"])
    x_cur = features.transform(current, artifact["levels"])
    used = set(artifact.get("features", features.feature_names()))
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    rows = []
    for col in features.numeric_columns:
        if col not in used or col in ("days_since_booking", "survived_share", "arr_weather_known") or col in volume_columns:
            continue
        value = psi_numeric(x_ref[col].values.astype(float), x_cur[col].values.astype(float))
        rows.append({"run_at": now, "feature": col, "psi": round(value, 4), "status": psi_status(value), "reference_rows": len(x_ref), "current_rows": len(x_cur)})
    for col in features.categorical_columns:
        if col not in used:
            continue
        value = psi_categorical(x_ref[col].astype(str), x_cur[col].astype(str))
        rows.append({"run_at": now, "feature": col, "psi": round(value, 4), "status": psi_status(value), "reference_rows": len(x_ref), "current_rows": len(x_cur)})
    frame = pd.DataFrame(rows)
    with db.db_lock:
        con = db.connect()
        try:
            con.register("incoming", frame)
            con.execute("delete from monitoring.drift where cast(run_at as date) = cast(? as date)", [now])
            con.execute("insert into monitoring.drift select * from incoming")
        finally:
            con.close()
    return len(frame)


# realised quality of booking time scores for one arrival window, every booking due to arrive in it is resolved
def performance_window(start, end):
    sql = "select s.p_cancel, b.is_canceled, s.model_version from scores.booking_time s join staging.stg_bookings b using (booking_id) where b.replay_arrival_date > ? and b.replay_arrival_date <= ?"
    frame = db.query_df(sql, [start, end])
    if len(frame) < 200 or frame["is_canceled"].nunique() < 2:
        return 0
    y = frame["is_canceled"].values
    p = frame["p_cancel"].values
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    version = frame["model_version"].mode()[0]
    row = [now, start, end, len(frame), round(float(y.mean()), 4), round(float(roc_auc_score(y, p)), 4), round(float(brier_score_loss(y, p)), 4), round(float(brier_score_loss(y, np.full(len(y), y.mean()))), 4), version]
    db.execute("delete from monitoring.performance where window_end = ?", [end])
    db.execute("insert into monitoring.performance values (?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
    return len(frame)


# weekly performance windows since the champion snapshot, only windows not computed yet plus the current one
def run_performance():
    today = clock.today()
    artifact = registry.load_champion()
    if artifact is None:
        return 0
    first_valid = pd.Timestamp(artifact["snapshot"]).date() + datetime.timedelta(days=clock.replay_shift_days) + datetime.timedelta(days=28)
    have = db.query_df("select window_end from monitoring.performance")["window_end"].astype(str).tolist()
    total = 0
    for back in range(52, -1, -1):
        end = today - datetime.timedelta(days=7 * back)
        if end < first_valid:
            continue
        if str(end) in have and back > 0:
            continue
        total = total + performance_window(end - datetime.timedelta(days=28), end)
    return total
