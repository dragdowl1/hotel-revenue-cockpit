import os
import datetime
import numpy as np
import pandas as pd
from app import db
from app import config

EVENTS_PATH = os.path.join(config.DBT_DIR, "seeds", "events.csv")

# calendar months of the real bookings, the base period of every index
base_start = pd.Timestamp("2015-07-01")
base_end = pd.Timestamp("2017-08-31")

# language groups with a wikipedia edition in the feed
attention_groups = ["en", "de", "fr", "es", "it", "pt", "nl", "pl", "sv", "ja", "zh", "ru"]

# elasticity of a market's demand to the euro against its currency, imf estimates around 0.1
fx_elasticity = -0.1


# monthly demand level, portugal hotel nights divided by the mean of the same calendar month in the base period, nowcast beyond the published months
def level_index(until):
    frame = db.query_df("select month, nights from raw.eurostat_nights where geo = 'PT' order by month")
    frame["month"] = pd.to_datetime(frame["month"])
    series = frame.set_index("month")["nights"].astype(float)
    growth = np.log(series / series.shift(12)).dropna()
    mean_growth = float(growth.iloc[-6:].mean())
    month = series.index[-1] + pd.DateOffset(months=1)
    while month <= pd.Timestamp(until):
        series[month] = float(series[month - pd.DateOffset(years=1)] * np.exp(mean_growth))
        month = month + pd.DateOffset(months=1)
    series = series.sort_index()
    base = series[base_start:base_end]
    base_by_month = base.groupby(base.index.month).mean()
    return series / series.index.month.map(base_by_month).values


# accommodation price level, the twelve month rolling mean of the index divided by its base period mean, the season stays with the atoms
def price_index(until):
    frame = db.query_df("select month, index from raw.price_index where geo = 'PT' order by month")
    if len(frame) == 0:
        return pd.Series(1.0, index=pd.date_range("2014-01-01", until, freq="MS"))
    frame["month"] = pd.to_datetime(frame["month"])
    series = frame.set_index("month")["index"].astype(float)
    trend = series.rolling(12, min_periods=6).mean().bfill()
    trend = trend.reindex(pd.date_range(trend.index[0], until, freq="MS")).ffill()
    return trend / trend[base_start:base_end].mean()


# monthly rate index per hotel region from the official average daily rate of four star hotels: for months from 2019 the official rate of the month relative to the same month of 2019 times the consumer price bridge from the base period to 2019, so the atoms keep their own seasonal shape and follow the published price development month by month; months beyond the last published take last year's month times the mean year on year growth of the last six published months; before 2019 and without the feed the consumer price trend applies
rate_regions = {"City Hotel": "Grande Lisboa", "Resort Hotel": "Algarve"}
rate_typology = "4*"


def rate_index(until, hotel):
    hicp = price_index(until)
    frame = db.query_df("select month, adr from raw.adr_regional where region = ? and typology = ? order by month", [rate_regions[hotel], rate_typology])
    if len(frame) < 24:
        return hicp
    frame["month"] = pd.to_datetime(frame["month"])
    adr = frame.groupby("month")["adr"].mean().astype(float)
    ref_year = int(adr.index.min().year)
    ref = adr[str(ref_year)]
    if len(ref) < 12:
        return hicp
    months = pd.date_range(pd.Timestamp(str(ref_year) + "-01-01"), until, freq="MS")
    yoy = (adr / adr.shift(12)).dropna()
    growth = float(yoy.tail(6).mean()) if len(yoy) >= 6 else 1.0
    filled = adr.reindex(months)
    for m in months:
        if pd.isna(filled[m]) and (m - pd.DateOffset(years=1)) in filled.index and not pd.isna(filled[m - pd.DateOffset(years=1)]):
            filled[m] = filled[m - pd.DateOffset(years=1)] * growth
    ratio = pd.Series([filled[m] / ref[ref.index.month == m.month].iloc[0] for m in months], index=months)
    bridge = float(hicp[str(ref_year)].mean())
    out = hicp.copy()
    out = out.reindex(pd.date_range(out.index[0], until, freq="MS")).ffill()
    out.loc[months] = ratio.values * bridge
    return out


# weekly attention per language group divided by the base period mean, weeks before the feed start borrow the same week one year later, groups or weeks without data count as neutral
def attention_index():
    frame = db.query_df("select project, date, views from raw.pageviews_daily")
    frame["date"] = pd.to_datetime(frame["date"])
    frame["group"] = frame["project"].str.split(".").str[0]
    frame["week"] = frame["date"].dt.to_period("W-SUN").dt.start_time
    weekly = frame.groupby(["group", "week"])["views"].sum().unstack(0).astype(float)
    weekly = weekly.clip(lower=1.0)
    smooth = np.log(weekly).rolling(4, min_periods=1).mean()
    base = smooth[base_start:base_end].mean()
    index = np.exp(smooth - base)
    first = index.index[0]
    early = pd.date_range("2013-12-30", first - pd.Timedelta(weeks=1), freq="W-MON")
    borrowed = index.reindex(early + pd.DateOffset(years=1), method="nearest")
    borrowed.index = early
    return pd.concat([borrowed, index]).clip(0.3, 3.0).fillna(1.0)


# monthly euro exchange rate per currency divided by the base period mean, currencies without a base period count as neutral
def fx_index(until):
    frame = db.query_df("select date, currency, rate from raw.fx_daily")
    frame["month"] = pd.to_datetime(frame["date"]).dt.to_period("M").dt.start_time
    monthly = frame.groupby(["month", "currency"])["rate"].mean().unstack(1)
    monthly = monthly.reindex(pd.date_range(monthly.index[0], until, freq="MS")).ffill().bfill()
    return (monthly / monthly[base_start:base_end].mean()).fillna(1.0)


# public holiday dates per country and school holiday dates per country
def holiday_sets():
    frame = db.query_df("select country, date, kind from raw.holidays")
    frame["date"] = pd.to_datetime(frame["date"])
    public = {c: set(p["date"]) for c, p in frame[frame["kind"] == "public"].groupby("country")}
    school = {c: set(p["date"]) for c, p in frame[frame["kind"] == "school"].groupby("country")}
    return public, school


# daily weather flags per location, archive first and the forecast for the days ahead
def weather_flags():
    frame = db.query_df("select location, date, temp_max, precip_mm, wind_max, weather_code, kind from raw.weather_daily")
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values("kind").drop_duplicates(subset=["location", "date"], keep="first")
    for col in ["temp_max", "precip_mm", "wind_max", "weather_code"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce").fillna(0)
    frame["hot_dry"] = ((frame["temp_max"] >= 28) & (frame["precip_mm"] < 1)).astype(int)
    frame["rainy"] = (frame["precip_mm"] >= 5).astype(int)
    frame["storm"] = ((frame["wind_max"] >= 60) | (frame["weather_code"] >= 95)).astype(int)
    return frame.set_index(["location", "date"])[["hot_dry", "rainy", "storm"]]


# the versioned event table with typed dates and market lists
def load_events():
    frame = pd.read_csv(EVENTS_PATH)
    frame["start_date"] = pd.to_datetime(frame["start_date"])
    frame["end_date"] = pd.to_datetime(frame["end_date"])
    frame["markets"] = frame["markets"].fillna("ALL").apply(lambda s: [] if s == "ALL" else s.split(";"))
    frame["segment_filter"] = frame["segment_filter"].fillna("").apply(lambda s: [] if s == "" else s.split(";"))
    for col in ["demand_multiplier", "cancel_flip_share", "noshow_share", "lead_factor", "ramp_days", "recovery_half_life_days"]:
        frame[col] = frame[col].astype(float)
    return frame


# multiplier of one event on one date, ramp in before the start, full inside, exponential recovery after the end
def event_weight(event, date):
    if date < event["start_date"] - pd.Timedelta(days=int(event["ramp_days"])):
        return 0.0
    if date < event["start_date"]:
        return float((date - (event["start_date"] - pd.Timedelta(days=int(event["ramp_days"])))).days / max(event["ramp_days"], 1))
    if date <= event["end_date"]:
        return 1.0
    days_after = (date - event["end_date"]).days
    return float(0.5 ** (days_after / max(event["recovery_half_life_days"], 1)))
