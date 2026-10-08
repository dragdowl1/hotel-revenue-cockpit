import os
import json
import datetime
import numpy as np
from app import db
import pandas as pd
from app import config
from app.cache import cached
from app.replay import clock
from typing import Annotated
from app.api.common import records
from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/demand")


# daily wikipedia pageviews of the lisbon article per language edition
@router.get("/pageviews")
def pageviews(days: Annotated[int, Query(ge=1, le=3660)] = 365):
    start = datetime.date.today() - datetime.timedelta(days=days)
    frame = db.query_df("select project, date, views from raw.pageviews_daily where date >= ? order by project, date", [start])
    return records(frame)


# weekly pageviews per language with the same week one year earlier
@router.get("/pageviews_weekly")
def pageviews_weekly(weeks: Annotated[int, Query(ge=1, le=520)] = 52):
    start = datetime.date.today() - datetime.timedelta(weeks=weeks + 53)
    frame = db.query_df("select project, date_trunc('week', date) as week, sum(views) as views from raw.pageviews_daily where date >= ? group by 1, 2 order by 1, 2", [start])
    return records(frame)


# weather forecast and recent archive for both hotel locations
@router.get("/weather")
def weather(past: Annotated[int, Query(ge=0, le=3660)] = 30):
    start = datetime.date.today() - datetime.timedelta(days=past)
    frame = db.query_df("select location, date, temp_max, temp_min, precip_mm, wind_max, weather_code, kind from raw.weather_daily where date >= ? order by location, date, kind", [start])
    return records(frame)


# upcoming public and school holidays by country
@router.get("/holidays")
def holidays(days: Annotated[int, Query(ge=1, le=3660)] = 90):
    today = datetime.date.today()
    frame = db.query_df(
        "select country, kind, min(date) as start_date, max(date) as end_date, name, count(*) as days from raw.holidays where date >= ? and date <= ? group by country, kind, name order by start_date, country",
        [today, today + datetime.timedelta(days=days)])
    return records(frame)


# monthly nights in hotels per country from eurostat
@router.get("/eurostat")
def eurostat():
    frame = db.query_df("select geo, month, nights from raw.eurostat_nights order by geo, month")
    return records(frame)


# official monthly average daily rate of four star hotels in the two hotel regions next to the realised rate of each hotel, for the rate calibration chart
@router.get("/adr")
def adr():
    official = db.query_df("select region, month, adr from raw.adr_regional where typology = '4*' and region in ('Grande Lisboa', 'Algarve') order by region, month")
    ours = db.query_df("select b.hotel, date_trunc('month', cast(s.replay_stay_date as date)) as month, sum(s.adr) / count(*) as adr from staging.stg_stay_nights s join staging.stg_bookings b using (booking_id) where s.is_canceled = 0 and b.replay_leave_books_date <= current_date and s.replay_stay_date >= '2019-01-01' group by 1, 2 order by 1, 2")
    return {"official": records(official), "hotels": records(ours), "regions": {"City Hotel": "Grande Lisboa", "Resort Hotel": "Algarve"}}


# euro exchange rates for the main source markets
@router.get("/fx")
def fx(days: Annotated[int, Query(ge=1, le=3660)] = 365):
    start = datetime.date.today() - datetime.timedelta(days=days)
    frame = db.query_df("select date, currency, rate from raw.fx_daily where date >= ? and currency in ('USD', 'GBP', 'BRL', 'CHF', 'PLN') order by currency, date", [start])
    return records(frame)


# bookings on the books by booker country for the coming days
@router.get("/source_markets")
@cached
def source_markets(days: Annotated[int, Query(ge=1, le=3660)] = 90):
    today = clock.today()
    frame = db.query_df(
        "select country, count(*) as bookings, round(sum(booking_value), 0) as value_on_books, round(avg(lead_time), 1) as avg_lead_time "
        "from staging.stg_bookings where replay_booking_date <= ? and replay_leave_books_date > ? and replay_arrival_date > ? and replay_arrival_date <= ? group by 1 order by 2 desc limit 15",
        [today, today, today, today + datetime.timedelta(days=days)])
    return records(frame)


# weekly arrivals forecast, backtest summary and eurostat nowcast from the forecasting notebook
@router.get("/forecast")
@cached
def forecast():
    path = os.path.join(config.MODELS_DIR, "forecast.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        out = json.load(f)
    today = clock.today()
    actual = db.query_df(
        "select hotel, date_trunc('week', cast(replay_arrival_date as date)) as week, count(*) as arrivals from staging.stg_bookings "
        "where is_canceled = 0 and replay_arrival_date > ? and replay_arrival_date <= ? group by 1, 2 order by 2, 1", [today - datetime.timedelta(weeks=26), today])
    out["actual"] = records(actual)
    out["nowcast_live"] = nowcast_live()
    return sanitize(out)


# replace nan and infinite floats by null anywhere in a nested structure
def sanitize(obj):
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    if isinstance(obj, float) and (obj != obj or obj in (float("inf"), float("-inf"))):
        return None
    return obj


# trend nowcast of portugal hotel nights for months not yet published, same month last year times the mean growth of the last six published months
def nowcast_live(window=6):
    frame = db.query_df("select month, nights from raw.eurostat_nights where geo = 'PT' order by month")
    if len(frame) < 18:
        return {}
    frame["month"] = pd.to_datetime(frame["month"])
    series = frame.set_index("month")["nights"]
    growth = np.log(series / series.shift(12)).dropna()
    mean_growth = float(growth.iloc[-window:].mean())
    last = series.index[-1]
    today = datetime.date.today()
    months, values = [], []
    month = last + pd.DateOffset(months=1)
    while month.date() < datetime.date(today.year, today.month, 1):
        base = series.get(month - pd.DateOffset(years=1))
        if base is None:
            break
        months.append(str(month.date()))
        values.append(round(float(base * np.exp(mean_growth))))
        month = month + pd.DateOffset(months=1)
    return {"months": months, "nights": values, "growth": round(mean_growth, 4), "last_published": str(last.date()), "window": window}
