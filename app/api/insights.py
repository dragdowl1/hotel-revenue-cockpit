import datetime
import numpy as np
import pandas as pd
from fastapi import APIRouter
from app.cache import cached
from app import db
from app.api.common import records
from app.replay import clock

router = APIRouter(prefix="/api/insights")


# share of eventual cancellations already known at each number of days before arrival, by segment
@router.get("/hazard")
@cached
def hazard():
    from app.analytics import hazard as hz
    return {"grid": hz.grid, "rows": hz.known_share(), "group_known_30": hz.group_share_known(30)}


# month over month change in cancel rate of new bookings split into within segment change and mix shift
@router.get("/mix_shift")
@cached
def mix_shift(months: int = 18):
    today = clock.today()
    frame = db.query_df(
        "select date_trunc('month', cast(replay_booking_date as date)) as month, market_segment, count(*) as bookings, avg(is_canceled) as cancel_rate "
        "from staging.stg_bookings where replay_leave_books_date <= current_date and replay_booking_date < date_trunc('month', cast(? as date)) and replay_booking_date >= date_trunc('month', cast(? as date)) group by 1, 2 order by 1",
        [today, today - datetime.timedelta(days=31 * (months + 1))])
    frame["month"] = pd.to_datetime(frame["month"]).dt.strftime("%Y-%m")
    months_list = sorted(frame["month"].unique())
    out = []
    for prev, cur in zip(months_list[:-1], months_list[1:]):
        a = frame[frame["month"] == prev].set_index("market_segment")
        b = frame[frame["month"] == cur].set_index("market_segment")
        segs = a.index.union(b.index)
        wa = a["bookings"].reindex(segs).fillna(0); wa = wa / wa.sum()
        wb = b["bookings"].reindex(segs).fillna(0); wb = wb / wb.sum()
        ra = a["cancel_rate"].reindex(segs).fillna(0)
        rb = b["cancel_rate"].reindex(segs).fillna(ra)
        within = float(((rb - ra) * wa).sum())
        mix = float(((wb - wa) * rb).sum())
        out.append({"month": cur, "cancel_rate": round(float((wb * rb).sum()), 4), "total_change": round(within + mix, 4), "within_segment": round(within, 4), "mix_shift": round(mix, 4), "ota_share": round(float(wb.get("Online TA", 0)), 4)})
    return out


# agent concentration and shrunken cancel rates per hotel, last 12 replay months of bookings
@router.get("/agents")
@cached
def agents():
    today = clock.today()
    frame = db.query_df(
        "select hotel, agent, count(*) as bookings, sum(is_canceled) as canceled, round(avg(adr), 2) as avg_adr, round(sum(case when is_canceled = 0 then booking_value else 0 end), 0) as realised_revenue, round(sum(case when is_canceled = 1 then booking_value else 0 end), 0) as lost_value "
        "from staging.stg_bookings where agent <> 'NULL' and replay_leave_books_date <= current_date and replay_arrival_date > ? and replay_arrival_date <= ? group by 1, 2", [today - datetime.timedelta(days=365), today])
    out = {"hotels": {}, "agents": []}
    k = 50.0
    for hotel, part in frame.groupby("hotel"):
        share = part["bookings"] / part["bookings"].sum()
        hhi = float((share ** 2).sum())
        top5 = float(part.sort_values("bookings", ascending=False)["bookings"].head(5).sum() / part["bookings"].sum())
        prior = float(part["canceled"].sum() / part["bookings"].sum())
        part = part.assign(shrunk_cancel_rate=((part["canceled"] + k * prior) / (part["bookings"] + k)).round(4), raw_cancel_rate=(part["canceled"] / part["bookings"]).round(4), share=share.round(4))
        ranked = part.sort_values("realised_revenue", ascending=False)
        total_revenue = float(part["realised_revenue"].sum())
        out["hotels"][hotel] = {"hhi": round(hhi, 4), "top5_share": round(top5, 4), "agents": int(len(part)), "prior_cancel_rate": round(prior, 4), "agent_revenue": round(total_revenue, 0), "top1_revenue_share": round(float(ranked["realised_revenue"].iloc[0]) / max(total_revenue, 1.0), 4), "top5_revenue_share": round(float(ranked["realised_revenue"].head(5).sum()) / max(total_revenue, 1.0), 4), "top_agent": str(ranked["agent"].iloc[0])}
        out["agents"].extend(records(part.sort_values("bookings", ascending=False).head(25)))
    return out


# party composition economics, adr per guest and cancellation by party type
@router.get("/party")
@cached
def party():
    frame = db.query_df(
        "select hotel, case when adults = 1 and children + babies = 0 then 'solo' when adults = 2 and children + babies = 0 then 'couple' when children + babies > 0 then 'family' else 'group of adults' end as party, "
        "count(*) as bookings, round(avg(is_canceled), 4) as cancel_rate, round(avg(adr), 2) as avg_adr, round(avg(adr / greatest(adults + children, 1)), 2) as adr_per_guest, round(avg(nights), 2) as avg_nights, round(avg(lead_time), 1) as avg_lead_time, round(avg(total_of_special_requests), 2) as special_requests, round(avg(required_car_parking_spaces), 3) as parking "
        "from staging.stg_bookings where adults > 0 and adr > 0 and replay_leave_books_date <= current_date group by 1, 2 order by 1, 3 desc")
    return records(frame)


# occupancy and adr by weekday and month, the seasonality grid
@router.get("/seasonality")
@cached
def seasonality():
    frame = db.query_df(
        "select hotel, extract(month from stay_date) as month, extract(isodow from stay_date) as dow, round(avg(occupancy), 4) as occupancy, round(avg(adr), 2) as adr, round(avg(revpar), 2) as revpar "
        "from marts.mart_daily_occupancy group by 1, 2, 3 order by 1, 2, 3")
    return records(frame)


# no show as a distinct outcome from cancellation, by segment
@router.get("/outcomes")
@cached
def outcomes():
    frame = db.query_df(
        "select hotel, market_segment, count(*) as bookings, round(avg(case when reservation_status = 'Canceled' then 1 else 0 end), 4) as canceled_rate, round(avg(case when reservation_status = 'No-Show' then 1 else 0 end), 4) as no_show_rate "
        "from staging.stg_bookings where replay_leave_books_date <= current_date group by 1, 2 having count(*) >= 200 order by 1, 3 desc")
    return records(frame)
