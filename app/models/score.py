import datetime
import numpy as np
import pandas as pd
from app import db
from app.models import features
from app.models import registry
from app.models import context_db
from app.models import preprocess
from app.replay import clock


# create the scoring tables if missing
def ensure_tables():
    db.execute("create schema if not exists scores")
    db.execute("create table if not exists scores.booking_time (booking_id bigint, p_cancel double, expected_loss double, model_version varchar, scored_at timestamp)")
    db.execute("create table if not exists scores.on_books (booking_id bigint, as_of date, p_cancel double, expected_loss double, model_version varchar, scored_at timestamp)")
    db.execute("create table if not exists scores.score_runs (run_at timestamp, kind varchar, rows integer, model_version varchar)")


# keep exactly the columns the artifact was trained on, missing ones as zero
def align_columns(x, artifact):
    cols = artifact.get("features", list(x.columns))
    for c in cols:
        if c not in x.columns:
            x[c] = 0
    return x[cols]


# apply the preprocessing saved with an artifact: percentile caps or median replacement, then the scaler
def preprocess_matrix(x, artifact):
    return preprocess.apply_preprocessing(x, artifact)


# calibrated cancellation probability for a frame with an as_of column, context in replay time unless told otherwise
def predict(artifact, frame, replay=True):
    enriched = context_db.enrich(frame, replay) if artifact.get("context", False) else frame
    x = features.transform(enriched, artifact["levels"])
    x = align_columns(x, artifact)
    x = preprocess_matrix(x, artifact)
    raw = artifact["model"].predict(x, num_iteration=artifact.get("best_iteration")) if artifact.get("family", "lightgbm") == "lightgbm" else artifact["model"].predict_proba(x)[:, 1]
    calibrator = artifact["calibrator"]
    if isinstance(calibrator, dict):
        return preprocess.calibrate(calibrator, raw, pd.to_datetime(frame["arrival_date"]).dt.month.values)
    low, high = artifact.get("clip", (0.0, 1.0))
    return np.clip(calibrator.predict(raw), low, high)


# write a scored frame into a table replacing rows with the same key
def write_scores(table, frame, key_sql):
    with db.db_lock:
        con = db.connect()
        try:
            con.register("incoming", frame)
            con.execute("delete from " + table + " where " + key_sql)
            con.execute("insert into " + table + " select * from incoming")
        finally:
            con.close()


# score bookings created since the last booking time run, the live stream
def score_new_bookings():
    artifact = registry.load_champion()
    if artifact is None:
        return 0
    last = db.query_df("select max(b.replay_booking_date) as d from scores.booking_time s join staging.stg_bookings b using (booking_id)")["d"][0]
    if pd.isna(last):
        last = pd.Timestamp(clock.today()) - pd.Timedelta(days=400)
    frame = clock.created_between(pd.Timestamp(last), pd.Timestamp(clock.today()))
    if len(frame) == 0:
        return 0
    p = predict(artifact, frame)
    out = pd.DataFrame({"booking_id": frame["booking_id"].values, "p_cancel": p, "expected_loss": p * frame["booking_value"].values, "model_version": artifact["version"], "scored_at": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)})
    write_scores("scores.booking_time", out, "booking_id in (select booking_id from incoming)")
    db.execute("insert into scores.score_runs values (?, 'booking_time', ?, ?)", [out["scored_at"][0], len(out), artifact["version"]])
    return len(out)


# rescore the whole on the books portfolio as of today
def score_on_books():
    artifact = registry.load_champion()
    if artifact is None:
        return 0
    as_of = clock.today()
    frame = clock.on_books(as_of)
    if len(frame) == 0:
        return 0
    p = predict(artifact, frame)
    out = pd.DataFrame({"booking_id": frame["booking_id"].values, "as_of": as_of, "p_cancel": p, "expected_loss": p * frame["booking_value"].values, "model_version": artifact["version"], "scored_at": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)})
    write_scores("scores.on_books", out, "as_of = '" + str(as_of) + "'")
    db.execute("insert into scores.score_runs values (?, 'on_books', ?, ?)", [out["scored_at"][0], len(out), artifact["version"]])
    db.execute("delete from scores.on_books where as_of < ?", [as_of - datetime.timedelta(days=120)])
    return len(out)
