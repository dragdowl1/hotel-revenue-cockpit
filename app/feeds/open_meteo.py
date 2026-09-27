import datetime
import pandas as pd
from app import config
from app import db
from app.feeds import common

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
daily_vars = "temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max,weather_code"

# the two hotel locations of the dataset, lisbon city and algarve resort
locations = {
    "city": (config.city_lat, config.city_lon),
    "resort": (config.resort_lat, config.resort_lon),
}


# convert an open meteo daily payload into rows for raw.weather_daily
def payload_to_frame(location, payload, kind):
    d = payload["daily"]
    frame = pd.DataFrame({
        "location": location,
        "date": pd.to_datetime(d["time"]).date,
        "temp_max": d["temperature_2m_max"],
        "temp_min": d["temperature_2m_min"],
        "precip_mm": d["precipitation_sum"],
        "wind_max": d["wind_speed_10m_max"],
        "weather_code": d["weather_code"],
        "kind": kind,
        "fetched_at": common.now_utc(),
    })
    return frame


# replace rows for the given location and kind with a fresh frame
def upsert(frame, location, kind):
    if len(frame) == 0:
        return 0
    with db.db_lock:
        con = db.connect()
        try:
            con.register("incoming", frame)
            con.execute("delete from raw.weather_daily where location = ? and kind = ? and date in (select date from incoming)", [location, kind])
            con.execute("insert into raw.weather_daily select * from incoming")
        finally:
            con.close()
    return len(frame)


# pull the 16 day forecast for both locations
def fetch_forecast():
    added = 0
    for location, (lat, lon) in locations.items():
        params = {"latitude": lat, "longitude": lon, "daily": daily_vars, "timezone": "Europe/Lisbon", "forecast_days": 16}
        payload = common.get_json(FORECAST_URL, params)
        added = added + upsert(payload_to_frame(location, payload, "forecast"), location, "forecast")
    return added


# pull historical daily weather for a date range from the era5 archive
def fetch_archive(start, end):
    added = 0
    for location, (lat, lon) in locations.items():
        params = {"latitude": lat, "longitude": lon, "daily": daily_vars, "timezone": "Europe/Lisbon", "start_date": str(start), "end_date": str(end)}
        payload = common.get_json(ARCHIVE_URL, params)
        added = added + upsert(payload_to_frame(location, payload, "archive"), location, "archive")
    return added


# backfill the archive for the dataset period and the last year if missing
def backfill():
    have = db.query_df("select count(*) as n from raw.weather_daily where kind = 'archive'")["n"][0]
    if have > 0:
        return 0
    added = fetch_archive(datetime.date(2015, 6, 1), datetime.date(2017, 9, 30))
    last_year = datetime.date.today() - datetime.timedelta(days=370)
    until = datetime.date.today() - datetime.timedelta(days=6)
    added = added + fetch_archive(last_year, until)
    return added


# keep the archive rolling, pull the days between the last archived date and six days ago
def refresh_archive():
    last = db.query_df("select max(date) as d from raw.weather_daily where kind = 'archive'")["d"][0]
    until = datetime.date.today() - datetime.timedelta(days=6)
    if pd.isna(last):
        return backfill()
    start = pd.Timestamp(last).date() + datetime.timedelta(days=1)
    if start > until:
        return 0
    return fetch_archive(start, until)


# backfill the era5 archive from a start date in yearly chunks, only the years with no archive rows yet
def backfill_history(start=datetime.date(2014, 1, 1)):
    have = db.query_df("select distinct extract(year from date) as y from raw.weather_daily where kind = 'archive'")["y"].astype(int).tolist()
    until = datetime.date.today() - datetime.timedelta(days=6)
    added = 0
    for year in range(start.year, until.year + 1):
        if year in have and year < until.year:
            continue
        y0 = max(start, datetime.date(year, 1, 1))
        y1 = min(datetime.date(year, 12, 31), until)
        added = added + fetch_archive(y0, y1)
    return added
