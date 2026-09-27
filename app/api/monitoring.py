import os
import math
import datetime
import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Header
from app import db
from app.api.common import records
from app.models import registry
from app import scheduler
from app.api import rm as rm_api
from app.api.market import load_json
from app.replay import clock

router = APIRouter(prefix="/api/monitoring")


# last run and freshness per feed
@router.get("/feeds")
def feeds():
    frame = db.query_df(
        "select feed, max(run_at) as last_run, arg_max(status, run_at) as last_status, arg_max(rows_added, run_at) as last_rows, arg_max(message, run_at) as last_message, "
        "count(*) filter (where status = 'error') as errors, count(*) as runs from raw.feed_runs group by feed order by feed")
    counts = db.query_df(
        "select 'weather_daily' as tbl, count(*) as rows, max(date) as latest from raw.weather_daily union all "
        "select 'fx_daily', count(*), max(date) from raw.fx_daily union all "
        "select 'holidays', count(*), max(date) from raw.holidays union all "
        "select 'pageviews_daily', count(*), max(date) from raw.pageviews_daily union all "
        "select 'eurostat_nights', count(*), max(month) from raw.eurostat_nights")
    return {"runs": records(frame), "tables": records(counts)}


# latest drift run per feature
@router.get("/drift")
def drift():
    frame = db.query_df("select * from monitoring.drift where run_at = (select max(run_at) from monitoring.drift) order by psi desc")
    history = db.query_df("select cast(run_at as date) as day, max(psi) as max_psi, count(*) filter (where status <> 'ok') as flagged from monitoring.drift group by 1 order by 1")
    return {"latest": records(frame), "history": records(history)}


# realised model quality over time
@router.get("/performance")
def performance():
    frame = db.query_df("select * from monitoring.performance order by window_end")
    return records(frame)


# model versions with their metrics and the champion pointer
@router.get("/models")
def models():
    out = []
    for name in registry.versions():
        out.append({"version": name, "champion": name == registry.champion_name(), "metrics": registry.metrics(name)})
    log = db.query_df("select * from monitoring.retrain_log order by run_at desc limit 20")
    return {"models": out, "retrain_log": records(log)}


# scheduler jobs and scoring runs
@router.get("/jobs")
def jobs():
    runs = db.query_df("select * from scores.score_runs order by run_at desc limit 20")
    return {"jobs": scheduler.status(), "score_runs": records(runs)}


# dbt run history
@router.get("/dbt")
def dbt():
    frame = db.query_df("select run_at, status, rows_added as ok_nodes, message from raw.feed_runs where feed = 'dbt_build' order by run_at desc limit 10")
    return records(frame)


# run one scheduled job now, protected by the admin token
@router.post("/run/{job_id}")
def run_job(job_id: str, x_admin_token: str = Header(default="")):
    if x_admin_token == "" or x_admin_token != os.environ.get("ADMIN_TOKEN", ""):
        raise HTTPException(status_code=403, detail="invalid token")
    jobs = {"daily_models": scheduler.job_daily_models, "feeds_daily": scheduler.job_feeds_daily, "feeds_hourly": scheduler.job_feeds_hourly, "replay_tick": scheduler.job_replay_tick, "weekly_retrain": scheduler.job_weekly_retrain, "reset_scores": scheduler.job_reset_scores, "monitoring": scheduler.job_monitoring, "export": scheduler.job_export, "load_airbnb": scheduler.job_load_airbnb, "recommendations": scheduler.job_recommendations, "refresh_notebooks": scheduler.job_refresh_notebooks, "airbnb_refresh": scheduler.job_airbnb_refresh, "airbnb_refresh_force": scheduler.job_airbnb_refresh_force, "synthetic_daily": scheduler.job_synthetic_daily, "synthetic_full": scheduler.job_synthetic_full}
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="unknown job")
    scheduler.scheduler.add_job(jobs[job_id], id="manual_" + job_id + "_" + str(int(datetime.datetime.now().timestamp())))
    return {"queued": job_id}


# loss and metric values of a learning curve at the best iteration
def curve_at_best(curves, best_iteration):
    curve = curves.get("learning_curve", {})
    if not curve or not best_iteration or best_iteration > len(curve.get("iteration", [])):
        return {}
    i = int(best_iteration) - 1
    return {k: curve[k][i] for k in ["train_loss", "test_loss", "train_auc", "test_auc"] if k in curve}


# backtest metrics of the live booking curve forecast, forecast rooms per stay date against realised rooms at weekly origins
def booking_curve_metrics():
    bt = rm_api.forecast_backtest()
    if not bt:
        return {}
    a = bt["by_hotel"].get("all", {})
    out = {"backtest MAE, rooms per stay date": a.get("mae"), "backtest RMSE": a.get("rmse"), "backtest MAPE": a.get("mape"), "backtest bias, forecast minus realised": a.get("bias"), "MAE of rooms on the books as forecast": a.get("mae_otb"), "RMSE of rooms on the books as forecast": a.get("rmse_otb")}
    for hotel in ["City Hotel", "Resort Hotel"]:
        h = bt["by_hotel"].get(hotel, {})
        out[hotel + " MAE"] = h.get("mae")
        out[hotel + " MAPE"] = h.get("mape")
    out["origins in backtest"] = bt.get("origins")
    out["stay dates evaluated"] = bt.get("stay_dates")
    return out


# every model behind the dashboard with its purpose, features and current metrics
@router.get("/catalog")
def catalog():
    from app.models import features
    out = []
    name = registry.champion_name()
    m = registry.metrics(name) if name else {}
    perf = db.query_df("select roc_auc, brier, brier_base, window_end from monitoring.performance order by window_end desc limit 1")
    live = records(perf)[0] if len(perf) else {}
    at_best = curve_at_best(registry.curves(name) if name else {}, m.get("best_iteration"))
    out.append({"name": "Cancellation model", "version": name, "type": "LightGBM classifier on landmark samples, time based validation cohort, isotonic calibration clipped to 0.005 to 0.99", "purpose": "Probability that a booking cancels, scored when it arrives and nightly for the whole portfolio. Feeds revenue at risk, expected cancellations, overbooking allowances and the risk page.", "trained": m.get("snapshot"), "features": m.get("feature_list") or features.feature_names(), "metrics": {"offline on the books ROC AUC": (m.get("on_books") or m.get("holdout") or {}).get("roc_auc"), "offline Brier": (m.get("on_books") or m.get("holdout") or {}).get("brier"), "offline Brier of base rate": (m.get("on_books") or {}).get("brier_base"), "offline expected calibration error": (m.get("on_books") or {}).get("ece"), "validation ROC AUC, arrivals of the 120 days before the snapshot": (m.get("validation") or {}).get("roc_auc"), "training window, years": m.get("window_years"), "training bookings": m.get("train_bookings"), "live ROC AUC, last 4 weeks of arrivals": live.get("roc_auc"), "live Brier": live.get("brier"), "live Brier of base rate": live.get("brier_base"), "best iteration": m.get("best_iteration"), "train log loss at best iteration": at_best.get("train_loss"), "validation log loss at best iteration": at_best.get("test_loss"), "train ROC AUC at best iteration": at_best.get("train_auc"), "validation ROC AUC at best iteration": at_best.get("test_auc"), "training rows": m.get("train_rows")}, "pages": ["Morning brief", "Recommendations", "Pace and forecast", "Cancellation risk"]})
    fc = load_json("forecast.json")
    if fc:
        bt = {r["hotel"] + " " + r["method"]: r["mape"] for r in fc.get("backtest", [])}
        fcs = fc.get("forecast", {})
        dg = fc.get("diagnostics", {})
        metrics = {}
        for hotel, f in fcs.items():
            metrics[hotel + " chosen method"] = f.get("method")
            metrics[hotel + " MAPE 8 weeks ahead"] = f.get("mape_backtest")
            metrics[hotel + " seasonal naive MAPE"] = f.get("mape_seasonal_naive")
            metrics[hotel + " origins in backtest"] = f.get("origins_in_backtest")
        for hotel, d in dg.items():
            metrics[hotel + " residual Ljung-Box p at lag 8"] = d.get("ljung_box_p_lag8")
            metrics[hotel + " ADF p value, level"] = d.get("adf_p_level")
        out.append({"name": "Demand forecast", "version": fc.get("generated_at", "")[:10], "type": fc.get("method", "Exponential smoothing with yearly seasonality per hotel") + "; chosen by a rolling backtest of 52 origins, 8 weeks ahead", "purpose": "Stayed arrivals per week for the next 8 weeks.", "trained": fc.get("generated_at", "")[:10], "features": ["weekly stayed arrivals history", "rooms already on the books per target week", "last year's pickup ratios by weeks ahead", "fourier terms of the yearly cycle", "last year's same week, the recent level, the year on year ratio and the week of year for the boosted model"], "metrics": metrics, "pages": ["Demand signals"]})
        nc = fc.get("nowcast", {})
        out.append({"name": "Eurostat nowcast", "version": fc.get("generated_at", "")[:10], "type": "Same month last year times the mean growth of the last six published months", "purpose": "Portugal hotel nights for the months Eurostat has not published yet.", "trained": fc.get("generated_at", "")[:10], "features": ["monthly nights history", "Wikipedia attention tested and rejected as a leading signal"], "metrics": {"rolling MAPE last 12 months": nc.get("mape"), "same month last year MAPE": nc.get("naive_mape"), "Wikipedia coefficient": nc.get("pageview_coef"), "Wikipedia p value": nc.get("pageview_pvalue")}, "pages": ["Demand signals"]})
    out.append({"name": "Booking curve forecast", "version": "live", "type": "Pickup ratios by weeks before arrival from last year's stay dates, applied to today's rooms on the books", "purpose": "Final rooms per stay date and forecast occupancy for the next 90 days.", "trained": "recomputed on every request", "features": ["rooms on the books per stay date", "last year's rooms on the books at the same distance", "last year's final rooms"], "metrics": booking_curve_metrics(), "pages": ["Morning brief", "Pace and forecast", "Recommendations"]})
    el = load_json("elasticity.json")
    if el:
        a = el.get("airbnb", {})
        out.append({"name": "Airbnb hedonic price model", "version": el.get("generated_at", "")[:10], "type": "LightGBM regression on log nightly price, five fold cross validation", "purpose": "Model value of a listing from its attributes; the gap between price and model value explains the share of the year booked.", "trained": el.get("generated_at", "")[:10], "features": ["accommodates", "bedrooms", "beds", "bathrooms", "minimum nights", "reviews", "review scores", "host listings", "superhost", "instant bookable", "amenity count", "distance to centre", "coordinates", "neighbourhood", "room type", "property type"], "metrics": {"cross validated R2 on log price": a.get("hedonic_r2"), "booked share per unit of price gap": a.get("booked_share_per_unit_price_gap"), "standard error": a.get("se")}, "pages": ["Lisbon market"]})
        pk = el.get("pickup", {}); c = el.get("cancellation", {}); ota = pk.get("online_ta", {}); ci = pk.get("bootstrap_ci", {}); plc = pk.get("placebo", {})
        out.append({"name": "Price elasticity", "version": el.get("generated_at", "")[:10], "type": pk.get("method", "pickup regression with fixed effects") + "; logit of cancellation on the rate relative to the segment and month median", "purpose": "Volume response to rate changes along the booking curve, used by the rate rule for the online agency channel; overall response is close to zero, contracted tour operator volume is not price driven.", "trained": el.get("generated_at", "")[:10], "features": ["daily pickup per arrival week", "rate offered that day", "days to arrival", "booking weekday", "rooms on the books", "pace of the last seven days", "source market attention", "hotel, segment and arrival week fixed effects"], "metrics": {"pickup elasticity, all segments": (pk.get("overall") or {}).get("elasticity"), "bootstrap interval low": ci.get("low"), "bootstrap interval high": ci.get("high"), "online agency elasticity": ota.get("elasticity"), "online agency p value": ota.get("p"), "placebo future rate coefficient": plc.get("future_rate_coef"), "placebo p value": plc.get("future_rate_p"), "cancellation points per 10 percent price": c.get("points_per_10pct_price")}, "pages": ["Recommendations"]})
    ca = load_json("causal_channel.json")
    if ca:
        bl = ca.get("by_lead", {})
        out.append({"name": "Channel effect", "version": ca.get("generated_at", "notebook")[:10], "type": ca.get("method", "Inverse probability weighting"), "purpose": "Effect of booking direct instead of through an online travel agency on cancellation, used by the direct booking incentive rule; the effect is absent for bookings made within a week and grows with lead time.", "trained": ca.get("generated_at", "notebook")[:10], "features": ["lead time", "nights", "adults", "children", "rate", "special requests", "repeat guest", "previous cancellations", "arrival month", "arrival weekday", "booking weekday", "block size", "country cancel history", "parking", "hotel", "country group", "customer type", "room type", "meal"], "metrics": {"naive difference": ca.get("naive_difference"), "doubly robust effect": ca.get("aipw_effect"), "effect in the overlap region, used": ca.get("ipw_effect"), "interval low": ca.get("ci_low"), "interval high": ca.get("ci_high"), "share of rows in overlap": ca.get("share_in_overlap"), "E value": ca.get("e_value"), "placebo effect on travelling with a baby": ca.get("placebo_effect"), "effect for bookings within 7 days": bl.get("0-7"), "effect beyond 90 days": bl.get("90+"), "largest remaining imbalance": ca.get("max_smd_after")}, "pages": ["Recommendations"]})
    rt = load_json("review_topics.json")
    if rt:
        out.append({"name": "Review embeddings", "version": rt.get("generated_at", "")[:10], "type": rt.get("model", ""), "purpose": "Sentence embeddings of recent Lisbon Airbnb reviews; clusters were too coarse to show, aspects on the market page use keyword lexicons instead.", "trained": rt.get("generated_at", "")[:10], "features": ["review text"], "metrics": {"reviews embedded": rt.get("reviews")}, "pages": []})
    from app.api.demand import sanitize
    return sanitize(out)


# current data and model alerts, one row per problem, empty when everything is healthy
@router.get("/alerts")
def alerts():
    from app.feeds import airbnb
    out = []
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    last = db.query_df("select feed, arg_max(status, run_at) as status, max(run_at) as run_at, arg_max(message, run_at) as message from raw.feed_runs group by feed")
    stale_hours = {"open_meteo_forecast": 6, "frankfurter_latest": 6, "wikimedia_refresh": 48, "eurostat": 48, "travelbi_adr": 48, "dbt_build": 48}
    for r in last.to_dict("records"):
        age = (now - r["run_at"].to_pydatetime()).total_seconds() / 3600 if r["run_at"] is not None else None
        if r["status"] == "error":
            out.append({"severity": "warning", "kind": "feed error", "item": r["feed"], "detail": (r["message"] or "")[:160], "since": str(r["run_at"])[:16]})
        if r["feed"] in stale_hours and age is not None and age > stale_hours[r["feed"]]:
            out.append({"severity": "warning", "kind": "feed stale", "item": r["feed"], "detail": "last run " + str(round(age)) + " hours ago", "since": str(r["run_at"])[:16]})
    snap = airbnb.snapshot_date()
    age_days = (now.date() - datetime.date.fromisoformat(snap)).days
    if age_days > 120:
        out.append({"severity": "info", "kind": "snapshot old", "item": "inside airbnb", "detail": "snapshot " + snap + " is " + str(age_days) + " days old", "since": snap})
    perf = db.query_df("select roc_auc, brier, brier_base, window_end from monitoring.performance order by window_end desc limit 1")
    if len(perf) and float(perf["brier"][0]) >= float(perf["brier_base"][0]):
        out.append({"severity": "critical", "kind": "model", "item": "cancellation model", "detail": "live Brier " + str(perf["brier"][0]) + " is no better than the base rate " + str(perf["brier_base"][0]), "since": str(perf["window_end"][0])[:10]})
    drift = db.query_df("select count(*) filter (where status = 'alert') as alerts from monitoring.drift where run_at = (select max(run_at) from monitoring.drift)")
    if len(drift) and int(drift["alerts"][0]) >= 6:
        out.append({"severity": "info", "kind": "drift", "item": "cancellation model", "detail": str(int(drift["alerts"][0])) + " features above the alert threshold", "since": ""})
    from app.synthetic import run
    batch = db.query_df("select max(batch_to) as d from synthetic.batches") if run.table_exists("synthetic", "batches") else pd.DataFrame({"d": [None]})
    if len(batch) and not pd.isna(batch["d"][0]) and (now.date() - pd.Timestamp(batch["d"][0]).date()).days > 2:
        out.append({"severity": "warning", "kind": "stream stale", "item": "synthetic stream", "detail": "last batch of bookings ended on " + str(batch["d"][0])[:10], "since": str(batch["d"][0])[:10]})
    scored = db.query_df("select max(run_at) as t from scores.score_runs where kind = 'on_books'")
    if len(scored) and scored["t"][0] is not None and (now - scored["t"][0].to_pydatetime()).total_seconds() > 36 * 3600:
        out.append({"severity": "critical", "kind": "scores stale", "item": "on the books scores", "detail": "last rescoring " + str(scored["t"][0])[:16], "since": str(scored["t"][0])[:16]})
    return {"count": len(out), "alerts": out, "checked_at": str(now)[:19]}


# the catalog flattened to one row per model and metric, optionally for one model
@router.get("/catalog_metrics")
def catalog_metrics(model: str = ""):
    out = []
    for m in catalog():
        if model and m["name"] != model:
            continue
        out.append({"model": m["name"], "metric": "version", "value": str(m.get("version"))})
        out.append({"model": m["name"], "metric": "type", "value": str(m.get("type"))})
        out.append({"model": m["name"], "metric": "trained", "value": str(m.get("trained"))})
        for k, v in m["metrics"].items():
            if v is None:
                continue
            out.append({"model": m["name"], "metric": k, "value": str(round(v, 4)) if isinstance(v, float) else str(v)})
        out.append({"model": m["name"], "metric": "features", "value": ", ".join(m["features"])})
        out.append({"model": m["name"], "metric": "used on", "value": ", ".join(m["pages"])})
    return out


# learning curve of one model as rows per iteration or origin, train against test for the loss and the main metric, forecast origins in replay dates with the seasonal naive test error as reference
@router.get("/curves")
def curves(model: str = "cancellation"):
    if model == "cancellation":
        name = registry.champion_name()
        c = registry.curves(name).get("learning_curve", {}) if name else {}
        return iteration_rows(c, "train_loss", "test_loss", "train_auc", "test_auc")
    if model.startswith("channel_"):
        c = load_json("causal_channel.json").get("learning_curves", {}).get(model.replace("channel_", ""), {})
        return iteration_rows(c, "train_loss", "test_loss", "train_auc", "test_auc")
    if model == "hedonic":
        c = load_json("elasticity.json").get("airbnb", {}).get("learning_curve", {})
        return iteration_rows(c, "train_rmse", "test_rmse", "train_r2", "test_r2")
    if model.startswith("forecast_"):
        hotel = "City Hotel" if model == "forecast_city" else "Resort Hotel"
        fc = load_json("forecast.json")
        rows = [r for r in fc.get("learning_curve", []) if r["hotel"] == hotel]
        chosen = fc.get("forecast", {}).get(hotel, {}).get("method")
        method = chosen if chosen in {r["method"] for r in rows} else "ets"
        shift = datetime.timedelta(days=clock.replay_shift_days)
        naive = {r["origin"]: r["test_mape"] for r in rows if r["method"] == "seasonal_naive"}
        return [{"origin": str(datetime.date.fromisoformat(r["origin"]) + shift), "origin_dataset_time": r["origin"], "method": r["method"], "train_mape": r["train_mape"], "test_mape": r["test_mape"], "naive_test_mape": naive.get(r["origin"])} for r in rows if r["method"] == method]
    raise HTTPException(status_code=404, detail="unknown model")


# rows per iteration from a learning curve dict
def iteration_rows(curve, train_loss, test_loss, train_metric, test_metric):
    if not curve:
        return []
    return [{"iteration": i, "train_loss": a, "test_loss": b, "train_metric": c, "test_metric": d} for i, a, b, c, d in zip(curve["iteration"], curve[train_loss], curve[test_loss], curve[train_metric], curve[test_metric])]


# feature importance of one model sorted from the most to the least important
@router.get("/importance")
def importance(model: str = "cancellation"):
    rows = []
    measure = "share of gain"
    if model == "cancellation":
        name = registry.champion_name()
        rows = registry.curves(name).get("importance", []) if name else []
    elif model.startswith("channel_"):
        rows = load_json("causal_channel.json").get("importance", {}).get(model.replace("channel_", ""), [])
    elif model == "hedonic":
        rows = load_json("elasticity.json").get("airbnb", {}).get("importance", [])
    elif model == "elasticity":
        rows = load_json("elasticity.json").get("pickup", {}).get("importance", [])
        measure = "absolute t statistic"
    else:
        raise HTTPException(status_code=404, detail="unknown model")
    key = "abs_t" if measure == "absolute t statistic" else "share"
    rows = [r for r in rows if isinstance(r.get(key), (int, float)) and math.isfinite(r.get(key))]
    rows = sorted(rows, key=lambda r: -r[key])
    return [{"rank": i + 1, "feature": r["feature"], "importance": r[key], "measure": measure} for i, r in enumerate(rows)]


# reliability bins of the champion: offline on the books at the snapshot from the metrics file, live from booking time scores of arrivals in the last eight weeks
@router.get("/calibration")
def calibration():
    name = registry.champion_name()
    m = registry.metrics(name) if name else {}
    label = m.get("reliability_label", "offline, on the books at the snapshot")
    out = [{"source": label, **r, "gap": round(r["observed"] - r["predicted"], 4)} for r in m.get("reliability_on_books", [])]
    today = clock.today()
    sql = "select s.p_cancel, b.is_canceled from scores.booking_time s join staging.stg_bookings b using (booking_id) where b.outcome_known and b.replay_arrival_date > ? and b.replay_arrival_date <= ?"
    frame = db.query_df(sql, [today - datetime.timedelta(days=56), today])
    if len(frame) >= 200:
        p = frame["p_cancel"].values
        y = frame["is_canceled"].astype(int).values
        edges = np.quantile(p, np.linspace(0, 1, 11))
        idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, 9)
        for b in range(10):
            mask = idx == b
            if mask.any():
                out.append({"source": "live, arrivals of the last 8 weeks", "bin": b + 1, "rows": int(mask.sum()), "predicted": round(float(p[mask].mean()), 4), "observed": round(float(y[mask].mean()), 4), "gap": round(float(y[mask].mean() - p[mask].mean()), 4)})
    return out


# results of the cancellation experiments on the stream, one row per setup with validation and on the books metrics
@router.get("/experiments")
def experiments():
    data = load_json("experiments_cancellation_synth.json")
    rows = []
    for r in data.get("results", []):
        rows.append({"setup": r.get("name"), "rows": r.get("rows"), "features": r.get("features"), "best_iteration": r.get("best_iteration"), "validation_auc": r.get("valid_auc"), "loss_gap": r.get("gap"), "on_books_auc": r.get("on_books_roc_auc"), "on_books_brier": r.get("on_books_brier"), "on_books_ece": r.get("on_books_ece"), "near_auc": r.get("near_roc_auc"), "future_auc": r.get("future_roc_auc")})
    return rows


# the event table of the synthetic stream, one row per event with its source
@router.get("/events")
def events():
    from app.synthetic import signals
    frame = signals.load_events()
    frame["markets"] = frame["markets"].apply(lambda m: "all" if len(m) == 0 else ", ".join(m))
    frame["segment_filter"] = frame["segment_filter"].apply(lambda m: "all" if len(m) == 0 else ", ".join(m))
    return records(frame)


# generation runs of the synthetic stream and the bookings it holds per hotel and year
@router.get("/synthetic")
def synthetic():
    from app.synthetic import run
    log = db.query_df("select * from synthetic.batches order by run_at desc limit 15") if run.table_exists("synthetic", "batches") else pd.DataFrame()
    per_year = db.query_df("select hotel, arrival_date_year as year, count(*) as bookings, round(avg(is_canceled), 4) as cancel_rate, round(avg(adr), 1) as adr, count(*) filter (where shock <> '') as shocked from synthetic.bookings group by 1, 2 order by 1, 2")
    return {"runs": records(log), "per_year": records(per_year)}
