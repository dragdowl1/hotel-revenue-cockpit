import datetime
from app import db
from app.cache import cached
from app.replay import clock
from typing import Annotated
from app.api.common import records
from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/risk")


# expected cancellations and revenue at risk per arrival week for the portfolio on the books
@router.get("/by_week")
@cached
def by_week(weeks: Annotated[int, Query(ge=1, le=520)] = 16):
    today = clock.today()
    frame = db.query_df(
        "select b.hotel, date_trunc('week', cast(b.replay_arrival_date as date)) as arrival_week, count(*) as bookings, round(sum(b.booking_value), 0) as value_on_books, "
        "round(sum(o.p_cancel), 1) as expected_cancellations, round(sum(o.expected_loss), 0) as revenue_at_risk, round(avg(o.p_cancel), 4) as avg_p_cancel "
        "from scores.on_books o join staging.stg_bookings b using (booking_id) "
        "where o.as_of = (select max(as_of) from scores.on_books) and b.replay_arrival_date > ? and b.replay_arrival_date <= ? group by 1, 2 order by 2, 1",
        [today, today + datetime.timedelta(weeks=weeks)])
    return records(frame)


# bookings with the highest expected loss
@router.get("/top")
@cached
def top(limit: Annotated[int, Query(ge=1, le=500)] = 40):
    frame = db.query_df(
        "select b.booking_id, b.hotel, cast(b.replay_arrival_date as date) as arrival_date, cast(b.replay_booking_date as date) as booking_date, b.lead_time, b.nights, b.market_segment, b.distribution_channel, b.deposit_type, b.customer_type, b.country, b.adr, round(b.booking_value, 0) as booking_value, round(o.p_cancel, 3) as p_cancel, round(o.expected_loss, 0) as expected_loss "
        "from scores.on_books o join staging.stg_bookings b using (booking_id) where o.as_of = (select max(as_of) from scores.on_books) and b.replay_arrival_date > current_date order by o.expected_loss desc limit ?", [limit])
    return records(frame)


# histogram of cancellation probabilities on the books
@router.get("/distribution")
@cached
def distribution():
    frame = db.query_df(
        "select b.hotel, floor(o.p_cancel * 20) / 20 as bucket, count(*) as bookings, round(sum(b.booking_value), 0) as value "
        "from scores.on_books o join staging.stg_bookings b using (booking_id) where o.as_of = (select max(as_of) from scores.on_books) group by 1, 2 order by 1, 2")
    return records(frame)


# risk split by deposit type, segment and lead time bucket
@router.get("/drivers")
@cached
def drivers():
    out = {}
    for name, expr in [("deposit_type", "b.deposit_type"), ("market_segment", "b.market_segment"), ("lead_bucket", "case when b.lead_time <= 7 then '0-7' when b.lead_time <= 30 then '8-30' when b.lead_time <= 90 then '31-90' else '90+' end"), ("customer_type", "b.customer_type")]:
        frame = db.query_df(
            "select " + expr + " as level, count(*) as bookings, round(avg(o.p_cancel), 4) as avg_p_cancel, round(sum(o.expected_loss), 0) as revenue_at_risk, round(sum(b.booking_value), 0) as value_on_books "
            "from scores.on_books o join staging.stg_bookings b using (booking_id) where o.as_of = (select max(as_of) from scores.on_books) group by 1 order by 4 desc")
        out[name] = records(frame)
    return out


# latest bookings scored on the replay stream, sorted by cancellation probability
@router.get("/stream")
def stream(limit: Annotated[int, Query(ge=1, le=500)] = 25):
    frame = db.query_df(
        "select * from (select b.booking_id, b.hotel, cast(b.replay_booking_date as date) as booking_date, cast(b.replay_arrival_date as date) as arrival_date, b.lead_time, b.nights, b.market_segment, b.deposit_type, b.country, b.adr, round(b.booking_value, 0) as booking_value, round(s.p_cancel, 3) as p_cancel, s.scored_at "
        "from scores.booking_time s join staging.stg_bookings b using (booking_id) order by b.replay_booking_date desc, s.scored_at desc, b.booking_id desc limit ?) order by p_cancel desc, booking_value desc", [limit])
    return records(frame)
