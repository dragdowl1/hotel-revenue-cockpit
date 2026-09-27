import pandas as pd
from app import db
from app.models import context
from app.replay import clock


# weather rows from the database
def load_weather():
    return db.query_df("select location, date, temp_max, precip_mm, kind from raw.weather_daily")


# holiday rows from the database
def load_holidays():
    return db.query_df("select country, date, kind from raw.holidays")


# add weather and holiday features, in replay time for live scoring or in dataset time for training
def enrich(frame, replay):
    weather = load_weather()
    holidays = load_holidays()
    if replay:
        out = frame.copy()
        out["replay_as_of"] = pd.to_datetime(out["as_of"]) + pd.Timedelta(days=clock.replay_shift_days)
        return context.add_context(out, weather, holidays, date_col="replay_arrival_date", as_of_col="replay_as_of")
    return context.add_context(frame, weather, holidays, date_col="arrival_date", as_of_col="as_of")
