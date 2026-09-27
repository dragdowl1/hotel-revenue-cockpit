import datetime
import numpy as np
import pandas as pd
from fastapi import APIRouter
from app.cache import cached
from app import db
from app.api.common import records
from app.replay import clock

router = APIRouter(prefix="/api/guests")

# window of bookings created that every view looks back on, and the minimum rows before a rate is reported
window_days = 365
min_rows = 50

# account lifecycle thresholds, days since the last booking and bookings in the prior year
dormant_days = 90
at_risk_min_bookings = 20

# persona from the party composition
persona_sql = "case when babies > 0 then 'family with baby' when children > 0 then 'family with children' when adults >= 3 then 'group of adults' when adults = 2 then 'couple' when adults = 1 then 'solo' else 'other' end"

# guest history group from the repeat flag and the cancellation history
history_sql = "case when previous_cancellations > 0 then 'prior cancellation' when is_repeated_guest = 1 then 'repeat guest' else 'first time' end"


# a rate only when enough rows support it
def safe_rate(num, den):
    if den is None or den < min_rows:
        return None
    return round(float(num) / float(den), 4)


# bookings created in the window whose outcome is known, grouped by a sql expression, with cancel rate and economics
def resolved_by(as_of, group_sql, extra_where=""):
    frame = db.query_df(
        "select hotel, " + group_sql + " as grp, count(*) as bookings, sum(is_canceled) as canceled, round(avg(adr), 2) as avg_adr, round(avg(adr / greatest(adults + children, 1)), 2) as adr_per_guest, round(avg(nights), 2) as avg_nights, round(avg(lead_time), 1) as avg_lead_time, "
        "round(avg(total_of_special_requests), 2) as special_requests, round(avg(case when market_segment = 'Direct' then 1.0 else 0.0 end), 4) as direct_share "
        "from staging.stg_bookings where replay_booking_date > ? and replay_booking_date <= ? and replay_leave_books_date <= ? " + extra_where + " group by 1, 2 order by 1, 3 desc",
        [as_of - datetime.timedelta(days=window_days), as_of, as_of])
    frame["cancel_rate"] = [safe_rate(c, n) for c, n in zip(frame["canceled"], frame["bookings"])]
    return frame


# headline numbers: repeat guests, cancellation history, engagement and dormant accounts, bookings created in the last year against the year before
@router.get("/kpis")
@cached
def kpis():
    as_of = clock.today()
    out = {"as_of": str(as_of), "window_days": window_days}
    for label, ref in [("current", as_of), ("last_year", as_of - datetime.timedelta(days=364))]:
        start = ref - datetime.timedelta(days=window_days)
        frame = db.query_df(
            "select count(*) as bookings, sum(is_repeated_guest) as repeat_bookings, sum(case when previous_cancellations > 0 then 1 else 0 end) as prior_cancel_bookings, sum(case when total_of_special_requests = 0 then 1 else 0 end) as no_request_bookings, sum(case when children + babies > 0 then 1 else 0 end) as family_bookings "
            "from staging.stg_bookings where replay_booking_date > ? and replay_booking_date <= ?", [start, ref])
        r = records(frame)[0]
        out[label] = {"bookings": r["bookings"], "repeat_share": safe_rate(r["repeat_bookings"], r["bookings"]), "prior_cancel_share": safe_rate(r["prior_cancel_bookings"], r["bookings"]), "no_request_share": safe_rate(r["no_request_bookings"], r["bookings"]), "family_share": safe_rate(r["family_bookings"], r["bookings"])}
    hist = resolved_by(as_of, history_sql)
    grp = hist.groupby("grp")[["bookings", "canceled"]].sum()
    out["cancel_rate_by_history"] = {k: safe_rate(v["canceled"], v["bookings"]) for k, v in grp.iterrows()}
    clean = grp[grp.index != "prior cancellation"].sum()
    out["cancel_rate_no_prior"] = safe_rate(clean["canceled"], clean["bookings"])
    req = resolved_by(as_of, "case when total_of_special_requests = 0 then 'none' else 'one or more' end")
    grp = req.groupby("grp")[["bookings", "canceled"]].sum()
    out["cancel_rate_by_requests"] = {k: safe_rate(v["canceled"], v["bookings"]) for k, v in grp.iterrows()}
    acc = accounts()
    out["accounts_at_risk"] = len(acc["at_risk"])
    out["accounts_at_risk_revenue"] = round(float(sum(a["prior_revenue"] for a in acc["at_risk"])))
    out["accounts_active"] = acc["summary"].get("active", 0)
    return out


# who books: party personas per hotel with share, cancellation and economics, bookings created in the last year with a known outcome
@router.get("/personas")
@cached
def personas():
    frame = resolved_by(clock.today(), persona_sql, "and adults > 0 and adr > 0")
    frame = frame.rename(columns={"grp": "persona"})
    frame["share"] = (frame["bookings"] / frame.groupby("hotel")["bookings"].transform("sum")).round(4)
    return records(frame)


# first time guests, repeat guests and guests with a prior cancellation, per hotel
@router.get("/history")
@cached
def history():
    frame = resolved_by(clock.today(), history_sql).rename(columns={"grp": "history"})
    frame["share"] = (frame["bookings"] / frame.groupby("hotel")["bookings"].transform("sum")).round(4)
    return records(frame)


# engagement: cancellation by number of special requests and by booking changes, per hotel
@router.get("/engagement")
@cached
def engagement():
    as_of = clock.today()
    frame = resolved_by(as_of, "cast(least(total_of_special_requests, 4) as integer)").rename(columns={"grp": "requests"})
    frame["requests"] = frame["requests"].astype(int).map(lambda v: "4 or more" if v >= 4 else str(v))
    frame["share"] = (frame["bookings"] / frame.groupby("hotel")["bookings"].transform("sum")).round(4)
    meal = resolved_by(as_of, "meal").rename(columns={"grp": "meal"})
    meal["share"] = (meal["bookings"] / meal.groupby("hotel")["bookings"].transform("sum")).round(4)
    fam = resolved_by(as_of, "case when children + babies > 0 then 'with children' else 'adults only' end").rename(columns={"grp": "party"})
    family_cancel = {k: safe_rate(v["canceled"], v["bookings"]) for k, v in fam.groupby("party")[["bookings", "canceled"]].sum().iterrows()}
    return {"requests": records(frame), "meal": records(meal), "family_cancel": family_cancel}


# account rfm for booking agents: recency, frequency, realised revenue and quality over the last year, lifecycle segment and the accounts at risk
@router.get("/accounts")
@cached
def accounts():
    as_of = clock.today()
    start = as_of - datetime.timedelta(days=window_days)
    prior_start = start - datetime.timedelta(days=window_days)
    frame = db.query_df(
        "select agent, max(replay_booking_date) as last_booking, min(replay_booking_date) as first_booking, "
        "count(*) filter (where replay_booking_date > ?) as bookings, "
        "sum(case when is_canceled = 0 and replay_arrival_date > ? and replay_arrival_date <= ? and replay_leave_books_date <= ? then booking_value else 0 end) as revenue, "
        "avg(case when replay_booking_date > ? and replay_leave_books_date <= ? then is_canceled end) as cancel_rate, "
        "count(*) filter (where replay_booking_date > ? and replay_leave_books_date <= ?) as resolved, "
        "avg(case when replay_booking_date > ? then adr end) as avg_adr, avg(case when replay_booking_date > ? then lead_time end) as avg_lead, "
        "count(*) filter (where replay_booking_date > ? and replay_booking_date <= ?) as prior_bookings, "
        "sum(case when is_canceled = 0 and replay_arrival_date > ? and replay_arrival_date <= ? and replay_leave_books_date <= ? then booking_value else 0 end) as prior_revenue "
        "from staging.stg_bookings where agent <> 'NULL' and replay_booking_date <= ? group by 1",
        [start, start, as_of, as_of, start, as_of, start, as_of, start, start, prior_start, start, prior_start, start, as_of, as_of])
    if len(frame) == 0:
        return {"as_of": str(as_of), "summary": {}, "segments": [], "at_risk": [], "top": []}
    hotel_adr = float(db.query_df("select avg(adr) as adr from staging.stg_bookings where replay_booking_date > ? and replay_booking_date <= ?", [start, as_of])["adr"][0] or 1.0)
    frame["recency_days"] = (pd.Timestamp(as_of) - pd.to_datetime(frame["last_booking"])).dt.days
    frame["tenure_days"] = (pd.Timestamp(as_of) - pd.to_datetime(frame["first_booking"])).dt.days
    frame["adr_index"] = (frame["avg_adr"] / hotel_adr).round(3)
    frame["cancel_rate"] = [safe_rate(c * n, n) if n and n >= min_rows else None for c, n in zip(frame["cancel_rate"].fillna(0), frame["resolved"])]
    active = frame[frame["bookings"] > 0]
    rev_q = active["revenue"].quantile([0.5, 0.8]).values if len(active) >= 5 else np.array([0.0, 0.0])
    def segment(r):
        if r["bookings"] == 0 and r["prior_bookings"] >= at_risk_min_bookings:
            return "dormant"
        if r["bookings"] == 0:
            return "inactive"
        if r["recency_days"] > dormant_days and r["prior_bookings"] >= at_risk_min_bookings:
            return "at risk"
        if r["tenure_days"] <= window_days:
            return "new"
        if r["revenue"] >= rev_q[1]:
            return "champion"
        if r["revenue"] >= rev_q[0]:
            return "loyal"
        return "occasional"
    frame["segment"] = frame.apply(segment, axis=1)
    order = ["champion", "loyal", "occasional", "new", "at risk", "dormant", "inactive"]
    seg = frame.groupby("segment").agg(accounts=("agent", "size"), revenue=("revenue", "sum"), prior_revenue=("prior_revenue", "sum"), bookings=("bookings", "sum")).reindex(order).dropna(how="all").fillna(0).reset_index()
    seg["revenue"] = seg["revenue"].round(0)
    seg["prior_revenue"] = seg["prior_revenue"].round(0)
    at_risk = frame[frame["segment"].isin(["at risk", "dormant"])].sort_values("prior_revenue", ascending=False).head(12)
    top = frame[frame["bookings"] > 0].sort_values("revenue", ascending=False).head(12)
    cols = ["agent", "segment", "recency_days", "bookings", "prior_bookings", "revenue", "prior_revenue", "cancel_rate", "adr_index", "avg_lead"]
    summary = {"active": int((frame["bookings"] > 0).sum()), "at_risk": int((frame["segment"] == "at risk").sum()), "dormant": int((frame["segment"] == "dormant").sum()), "hotel_adr": round(hotel_adr, 2), "dormant_days": dormant_days, "at_risk_min_bookings": at_risk_min_bookings, "window_days": window_days}
    return {"as_of": str(as_of), "summary": summary, "segments": records(seg), "at_risk": records(at_risk[cols].round(2)), "top": records(top[cols].round(2))}


# source markets: bookings created in the last year against the year before, with cancellation, lead time and stay length per country
@router.get("/markets")
@cached
def markets(top: int = 12):
    as_of = clock.today()
    start = as_of - datetime.timedelta(days=window_days)
    prior_start = start - datetime.timedelta(days=window_days)
    frame = db.query_df(
        "select country, count(*) filter (where replay_booking_date > ?) as bookings, count(*) filter (where replay_booking_date <= ?) as bookings_last_year, "
        "sum(is_canceled) filter (where replay_booking_date > ? and replay_leave_books_date <= ?) as canceled, count(*) filter (where replay_booking_date > ? and replay_leave_books_date <= ?) as resolved, "
        "round(avg(lead_time) filter (where replay_booking_date > ?), 1) as avg_lead_time, round(avg(nights) filter (where replay_booking_date > ?), 2) as avg_nights, round(avg(adr) filter (where replay_booking_date > ?), 2) as avg_adr, "
        "round(avg(case when market_segment = 'Direct' then 1.0 else 0.0 end) filter (where replay_booking_date > ?), 4) as direct_share, "
        "round(sum(case when is_canceled = 0 and replay_booking_date > ? and replay_leave_books_date <= ? then booking_value else 0 end), 0) as realised_revenue "
        "from staging.stg_bookings where replay_booking_date > ? and replay_booking_date <= ? group by 1 order by 2 desc limit ?",
        [start, start, start, as_of, start, as_of, start, start, start, start, start, as_of, prior_start, as_of, top])
    frame["cancel_rate"] = [safe_rate(c, n) for c, n in zip(frame["canceled"], frame["resolved"])]
    frame["growth"] = [round(float(b) / float(l) - 1, 4) if l and l >= min_rows else None for b, l in zip(frame["bookings"], frame["bookings_last_year"])]
    return records(frame)


# booking month cohorts: how each month's bookings turned out, and how many survive each number of days after booking by channel
@router.get("/cohorts")
@cached
def cohorts(months: int = 15):
    as_of = clock.today()
    first = (pd.Timestamp(as_of) - pd.DateOffset(months=months)).replace(day=1).date()
    frame = db.query_df(
        "select date_trunc('month', cast(replay_booking_date as date)) as cohort, count(*) as bookings, sum(booking_value) as booked_value, "
        "sum(case when reservation_status = 'Check-Out' and replay_arrival_date <= ? then booking_value else 0 end) as stayed_value, "
        "sum(case when reservation_status = 'Canceled' and replay_leave_books_date <= ? then booking_value else 0 end) as canceled_value, "
        "sum(case when reservation_status = 'No-Show' and replay_arrival_date <= ? then booking_value else 0 end) as no_show_value "
        "from staging.stg_bookings where replay_booking_date >= ? and replay_booking_date <= ? group by 1 order by 1", [as_of, as_of, as_of, first, as_of])
    frame["cohort"] = pd.to_datetime(frame["cohort"]).dt.strftime("%Y-%m")
    for col in ["stayed", "canceled", "no_show"]:
        frame[col + "_share"] = (frame[col + "_value"] / frame["booked_value"].clip(lower=1)).round(4)
    frame["open_share"] = (1 - frame["stayed_share"] - frame["canceled_share"] - frame["no_show_share"]).clip(lower=0).round(4)
    frame["booked_value"] = frame["booked_value"].round(0)
    out = {"as_of": str(as_of), "outcomes": records(frame[["cohort", "bookings", "booked_value", "stayed_share", "canceled_share", "no_show_share", "open_share"]])}
    grid = [0, 7, 14, 30, 60, 90, 120, 180]
    mature = db.query_df(
        "select market_segment, count(*) as bookings, " + ", ".join("avg(case when is_canceled = 1 and replay_leave_books_date <= replay_booking_date + to_days(" + str(d) + ") then 0.0 else 1.0 end) as s" + str(d) for d in grid) +
        " from staging.stg_bookings where replay_booking_date > ? and replay_booking_date <= ? and replay_leave_books_date <= ? group by 1 having count(*) >= ? order by 2 desc",
        [as_of - datetime.timedelta(days=window_days + 180), as_of - datetime.timedelta(days=180), as_of, min_rows * 4])
    out["survival_grid"] = grid
    out["survival"] = records(mature.round(4))
    return out


# market segments per hotel for the arrivals of the last year with a known outcome: bookings, cancellation, no shows, realised revenue and rate
@router.get("/segments")
@cached
def segments():
    as_of = clock.today()
    frame = db.query_df(
        "select hotel, market_segment, count(*) as bookings, round(avg(is_canceled), 4) as cancel_rate, round(avg(case when reservation_status = 'No-Show' then 1.0 else 0.0 end), 4) as no_show_rate, "
        "round(sum(case when is_canceled = 0 then booking_value else 0 end), 0) as realised_revenue, round(sum(case when is_canceled = 1 then booking_value else 0 end), 0) as lost_value, round(avg(adr), 2) as avg_adr, round(avg(lead_time), 1) as avg_lead_time "
        "from staging.stg_bookings where replay_arrival_date > ? and replay_arrival_date <= ? and replay_leave_books_date <= ? group by 1, 2 having count(*) >= ? order by 1, 6 desc",
        [as_of - datetime.timedelta(days=window_days), as_of, as_of, min_rows])
    frame["share"] = (frame["bookings"] / frame.groupby("hotel")["bookings"].transform("sum")).round(4)
    return records(frame)
