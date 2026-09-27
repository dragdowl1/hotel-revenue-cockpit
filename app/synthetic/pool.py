import numpy as np
import pandas as pd
from app import db

# booker country to the language group whose wikipedia attention stands for that market
country_group = {"PRT": "pt", "BRA": "pt", "GBR": "en", "IRL": "en", "USA": "en", "AUS": "en", "CAN": "en", "NZL": "en", "DEU": "de", "AUT": "de", "CHE": "de", "FRA": "fr", "BEL": "fr", "LUX": "fr", "ESP": "es", "ARG": "es", "MEX": "es", "ITA": "it", "NLD": "nl", "POL": "pl", "SWE": "sv", "NOR": "sv", "DNK": "sv", "FIN": "sv", "JPN": "ja", "CHN": "zh", "RUS": "ru", "UKR": "ru"}

# booker country to the currency of that market, euro countries left out
country_currency = {"GBR": "GBP", "USA": "USD", "BRA": "BRL", "CHE": "CHF", "POL": "PLN", "SWE": "SEK", "NOR": "NOK", "CHN": "CNY", "JPN": "JPY", "CAN": "CAD", "AUS": "AUD"}

# region of each hotel for regional events
hotel_region = {"City Hotel": "city", "Resort Hotel": "resort"}

# rooms per hotel
capacity = {"City Hotel": 270, "Resort Hotel": 260}

month_names = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


# the real bookings as sampling atoms with typed dates, the cancellation offset and the market group
def load_pool():
    frame = db.query_df("select * from raw.bookings")
    month_number = {name: i + 1 for i, name in enumerate(month_names)}
    frame["arrival_date"] = pd.to_datetime(pd.DataFrame({"year": frame["arrival_date_year"], "month": frame["arrival_date_month"].map(month_number), "day": frame["arrival_date_day_of_month"]}))
    frame["booking_date"] = frame["arrival_date"] - pd.to_timedelta(frame["lead_time"], unit="D")
    frame["nights"] = frame["stays_in_weekend_nights"] + frame["stays_in_week_nights"]
    frame["children"] = frame["children"].fillna(0).astype(int)
    frame["country"] = frame["country"].fillna("UNK")
    frame["agent"] = frame["agent"].fillna("NULL")
    frame["company"] = frame["company"].fillna("NULL")
    status_date = pd.to_datetime(frame["reservation_status_date"])
    frame["cancel_offset"] = np.where(frame["is_canceled"] == 1, (frame["arrival_date"] - status_date).dt.days.clip(lower=0), 0)
    frame["arrival_dow"] = frame["arrival_date"].dt.dayofweek
    frame["arrival_month"] = frame["arrival_date"].dt.month
    frame["group"] = frame["country"].map(country_group).fillna("other")
    frame["family"] = (frame["children"] + frame["babies"] > 0).astype(int)
    frame["flexible"] = (frame["deposit_type"] == "No Deposit").astype(int)
    frame = frame[(frame["adr"] >= 0) & (frame["adr"] < 1000) & (frame["nights"] > 0) & (frame["adults"] > 0)].reset_index(drop=True)
    return frame


# mean gross bookings per hotel and arrival week of the year in the real data, smoothed over three weeks
def weekly_profile(pool):
    weeks = pool.assign(week=pool["arrival_date"].dt.to_period("W-SUN").dt.start_time)
    counts = weeks.groupby(["hotel", "week"]).size().rename("n").reset_index()
    counts["woy"] = counts["week"].dt.isocalendar().week.astype(int).clip(upper=52)
    out = {}
    for hotel, part in counts.groupby("hotel"):
        by_woy = part.groupby("woy")["n"].mean().reindex(range(1, 53))
        by_woy = by_woy.interpolate(limit_direction="both")
        wrapped = pd.concat([by_woy.iloc[-1:], by_woy, by_woy.iloc[:1]])
        out[hotel] = wrapped.rolling(3, center=True).mean().iloc[1:-1].values
    return out


# share of arrivals per weekday, per hotel
def dow_profile(pool):
    return {hotel: part["arrival_dow"].value_counts(normalize=True).reindex(range(7)).fillna(0).values for hotel, part in pool.groupby("hotel")}


# mean monthly gross bookings per hotel over the calendar months of the real data, the base of the level index
def monthly_base(pool):
    months = pool.groupby(["hotel", pool["arrival_date"].dt.to_period("M")]).size().rename("n").reset_index()
    months["month"] = months["arrival_date"].dt.month
    return months.groupby(["hotel", "month"])["n"].mean()
