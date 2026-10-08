import os
import json
import datetime
from app import db
from app import config
from app.cache import cached
from app.replay import clock
from typing import Annotated
from app.api.common import records
from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/overview")


# headline numbers as of the replay date with the same window one year earlier
@router.get("/kpis")
@cached
def kpis(days: Annotated[int, Query(ge=1, le=3660)] = 30):
    today = clock.today()
    out = {"as_of": str(today), "window_days": days}
    for label, ref in [("current", today), ("last_year", today - datetime.timedelta(days=364))]:
        start = ref - datetime.timedelta(days=days)
        realised = db.query_df(
            "select count(*) as bookings_created, round(avg(case when replay_leave_books_date <= current_date then is_canceled end), 4) as cancel_rate_created, "
            "round(sum(case when is_canceled = 0 and replay_leave_books_date <= current_date then booking_value else 0 end), 0) as value_created "
            "from staging.stg_bookings where replay_booking_date > ? and replay_booking_date <= ?", [start, ref])
        stays = db.query_df(
            "select round(avg(occupancy), 4) as occupancy, round(avg(adr), 2) as adr, round(avg(revpar), 2) as revpar, round(sum(room_revenue), 0) as room_revenue "
            "from marts.mart_daily_occupancy where replay_stay_date > ? and replay_stay_date <= ?", [start, ref])
        books = db.query_df(
            "select count(*) as on_books, round(sum(booking_value), 0) as value_on_books "
            "from staging.stg_bookings where replay_booking_date <= ? and replay_leave_books_date > ? and replay_arrival_date > ?", [ref, ref, ref])
        out[label] = {**records(realised)[0], **records(stays)[0], **records(books)[0]}
    risk = db.query_df("select round(sum(expected_loss), 0) as revenue_at_risk, round(avg(p_cancel), 4) as avg_p_cancel, count(*) as scored from scores.on_books where as_of = (select max(as_of) from scores.on_books)")
    out["risk"] = records(risk)[0]
    return out


# realised monthly kpis per hotel up to the current replay month
@router.get("/monthly")
@cached
def monthly(months: Annotated[int, Query(ge=1, le=240)] = 24):
    today = clock.today()
    start = datetime.date(today.year, today.month, 1) - datetime.timedelta(days=31 * months)
    frame = db.query_df(
        "select hotel, cast(replay_month as date) as month, bookings, canceled, cancel_rate, room_nights, revenue, adr, occupancy, revpar, avg_lead_time, repeat_share "
        "from marts.mart_monthly_kpis where replay_month >= ? and replay_month < date_trunc('month', cast(? as date)) order by month, hotel", [start, today])
    return records(frame)


# realised occupancy for the past days and on the books occupancy for the coming days
@router.get("/daily")
@cached
def daily(past: Annotated[int, Query(ge=1, le=3660)] = 60, future: Annotated[int, Query(ge=1, le=3660)] = 90):
    today = clock.today()
    start = today - datetime.timedelta(days=past)
    end = today + datetime.timedelta(days=future)
    past_frame = db.query_df(
        "select hotel, cast(replay_stay_date as date) as date, rooms_sold, capacity, occupancy, adr, revpar, 'realised' as kind "
        "from marts.mart_daily_occupancy where replay_stay_date > ? and replay_stay_date <= ?", [start, today])
    future_frame = db.query_df(
        "with cap as (select 'Resort Hotel' as hotel, 260 as capacity union all select 'City Hotel', 270), "
        "n as (select s.hotel, cast(s.replay_stay_date as date) as date, count(*) as rooms_on_books, sum(coalesce(1 - o.p_cancel, 1)) as rooms_expected, avg(s.adr) as adr "
        "from staging.stg_stay_nights s join staging.stg_bookings b using (booking_id) "
        "left join scores.on_books o on o.booking_id = s.booking_id and o.as_of = (select max(as_of) from scores.on_books) "
        "where b.replay_booking_date <= ? and b.replay_leave_books_date > ? and s.replay_stay_date > ? and s.replay_stay_date <= ? group by 1, 2) "
        "select n.hotel, n.date, n.rooms_on_books as rooms_sold, cap.capacity, round(n.rooms_on_books / cap.capacity, 4) as occupancy, round(n.rooms_expected / cap.capacity, 4) as occupancy_expected, round(n.adr, 2) as adr, round(n.rooms_expected * n.adr / cap.capacity, 2) as revpar, 'on_books' as kind "
        "from n join cap using (hotel) order by date, hotel", [today, today, today, end])
    return {"past": records(past_frame), "future": records(future_frame)}


# booking pace, bookings created per week for the current and the previous replay year
@router.get("/pace")
@cached
def pace(weeks: Annotated[int, Query(ge=1, le=520)] = 26):
    today = clock.today()
    frame = db.query_df(
        "select hotel, date_trunc('week', cast(replay_booking_date as date)) as week, sum(bookings) as bookings, sum(canceled) as canceled, round(sum(booked_value), 0) as booked_value, round(avg(avg_lead_time), 1) as avg_lead_time "
        "from marts.mart_booking_pace where replay_booking_date > ? and replay_booking_date <= ? group by 1, 2 order by 2, 1",
        [today - datetime.timedelta(weeks=weeks + 52), today])
    return records(frame)


# kpis by market segment and distribution channel
@router.get("/segments")
@cached
def segments():
    frame = db.query_df("select * from marts.mart_segment_kpis where bookings >= 50 order by revenue desc")
    return records(frame)


# partner economics per booking agent
@router.get("/agents")
@cached
def agents(limit: Annotated[int, Query(ge=1, le=500)] = 30):
    frame = db.query_df("select * from marts.mart_agent_economics where agent <> 'NULL' order by realised_revenue desc limit ?", [limit])
    return records(frame)


# causal estimate of the booking channel effect from the causal notebook
@router.get("/causal")
def causal():
    path = os.path.join(config.MODELS_DIR, "causal_channel.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)
