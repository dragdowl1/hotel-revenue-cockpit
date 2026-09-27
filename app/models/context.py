import numpy as np
import pandas as pd

# days ahead for which a weather forecast is available
forecast_days = 16

# booker country codes of the dataset mapped to holiday feed codes
iso3_to_iso2 = {"PRT": "PT", "GBR": "GB", "FRA": "FR", "ESP": "ES", "DEU": "DE", "ITA": "IT", "IRL": "IE", "BEL": "BE", "BRA": "BR", "NLD": "NL", "USA": "US", "CHE": "CH", "CHN": "CN", "AUT": "AT", "SWE": "SE"}

# hotel to weather location
hotel_location = {"City Hotel": "city", "Resort Hotel": "resort"}

# extra feature columns produced here
context_columns = ["arr_temp_max", "arr_precip_mm", "arr_weather_known", "stay_home_holidays", "stay_home_school_share", "stay_pt_holidays", "arrival_near_pt_holiday"]


# monthly climatology per location from archive weather
def climatology(weather):
    archive = weather[weather["kind"] == "archive"].copy()
    archive["month"] = pd.to_datetime(archive["date"]).dt.month
    clim = archive.groupby(["location", "month"])[["temp_max", "precip_mm"]].mean().reset_index()
    return clim


# weather at arrival as known at the observation date, actual within the forecast window else climatology
def weather_features(frame, weather, date_col, as_of_col):
    out = frame[[date_col, as_of_col, "hotel"]].copy()
    out["arrival"] = pd.to_datetime(out[date_col]).dt.normalize()
    out["as_of"] = pd.to_datetime(out[as_of_col]).dt.normalize()
    out["location"] = out["hotel"].map(hotel_location)
    out["month"] = out["arrival"].dt.month
    days_ahead = (out["arrival"] - out["as_of"]).dt.days
    known = days_ahead <= forecast_days
    w = weather.copy()
    w["date"] = pd.to_datetime(w["date"]).dt.normalize()
    w = w.sort_values("kind").drop_duplicates(subset=["location", "date"], keep="first")
    actual = out.merge(w[["location", "date", "temp_max", "precip_mm"]], left_on=["location", "arrival"], right_on=["location", "date"], how="left")
    clim = out.merge(climatology(weather), on=["location", "month"], how="left")
    temp = np.where(known & actual["temp_max"].notna().values, actual["temp_max"].values, clim["temp_max"].values)
    precip = np.where(known & actual["precip_mm"].notna().values, actual["precip_mm"].values, clim["precip_mm"].values)
    result = pd.DataFrame(index=frame.index)
    result["arr_temp_max"] = np.nan_to_num(temp.astype(float), nan=float(np.nanmean(clim["temp_max"])))
    result["arr_precip_mm"] = np.nan_to_num(precip.astype(float), nan=0.0)
    result["arr_weather_known"] = (known & actual["temp_max"].notna().values).astype(int)
    return result


# holiday counts over the stay nights for the booker country and for portugal
def holiday_features(frame, holidays, date_col):
    h = holidays.copy()
    h["date"] = pd.to_datetime(h["date"]).dt.normalize()
    public = set(zip(h.loc[h["kind"] == "public", "country"], h.loc[h["kind"] == "public", "date"]))
    school = set(zip(h.loc[h["kind"] == "school", "country"], h.loc[h["kind"] == "school", "date"]))
    pt_dates = set(h.loc[(h["kind"] == "public") & (h["country"] == "PT"), "date"])
    arrival = pd.to_datetime(frame[date_col]).dt.normalize().values
    nights = np.clip(frame["nights"].values.astype(int), 1, 30)
    home = frame["country"].map(iso3_to_iso2).fillna("").values
    home_hol = np.zeros(len(frame))
    home_school = np.zeros(len(frame))
    pt_hol = np.zeros(len(frame))
    near_pt = np.zeros(len(frame))
    day = pd.Timedelta(days=1)
    for i in range(len(frame)):
        start = pd.Timestamp(arrival[i])
        for k in range(nights[i]):
            d = start + k * day
            if (home[i], d) in public:
                home_hol[i] = home_hol[i] + 1
            if (home[i], d) in school:
                home_school[i] = home_school[i] + 1
            if d in pt_dates:
                pt_hol[i] = pt_hol[i] + 1
        for k in range(-3, 4):
            if (start + k * day) in pt_dates:
                near_pt[i] = 1
    result = pd.DataFrame(index=frame.index)
    result["stay_home_holidays"] = home_hol
    result["stay_home_school_share"] = home_school / nights
    result["stay_pt_holidays"] = pt_hol
    result["arrival_near_pt_holiday"] = near_pt
    return result


# add all context features to a bookings frame observed at as_of
def add_context(frame, weather, holidays, date_col="arrival_date", as_of_col="as_of"):
    out = frame.copy()
    wf = weather_features(out, weather, date_col, as_of_col)
    hf = holiday_features(out, holidays, date_col)
    for col in wf.columns:
        out[col] = wf[col].values
    for col in hf.columns:
        out[col] = hf[col].values
    return out
