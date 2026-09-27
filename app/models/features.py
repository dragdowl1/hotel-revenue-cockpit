import numpy as np
import pandas as pd

# columns that leak the outcome or are only known after the stay, never used as features
leak_columns = ["reservation_status", "reservation_status_date", "assigned_room_type", "booking_changes", "is_canceled"]

# categorical feature columns
categorical_columns = ["hotel", "meal", "country", "market_segment", "distribution_channel", "reserved_room_type", "deposit_type", "agent", "company", "customer_type", "persona"]

# numeric feature columns of the first feature set
numeric_columns_v1 = ["lead_time", "nights", "stays_in_weekend_nights", "stays_in_week_nights", "adults", "children", "babies", "is_repeated_guest", "previous_cancellations", "previous_bookings_not_canceled", "days_in_waiting_list", "adr", "required_car_parking_spaces", "total_of_special_requests", "arrival_month", "arrival_week", "arrival_dow", "booking_month", "booking_dow", "adr_per_person", "has_company", "has_agent", "days_since_booking", "days_to_arrival", "survived_share", "arr_temp_max", "arr_precip_mm", "arr_weather_known", "stay_home_holidays", "stay_home_school_share", "stay_pt_holidays", "arrival_near_pt_holiday", "same_day_clones", "agent_prior_rate", "agent_prior_n", "country_prior_rate", "rel_price", "is_portugal", "total_guests", "weekend_share", "prev_cancel_ratio"]

# guest and account features added with the second feature set, all known when the booking is made
numeric_columns_v2 = ["has_prior_cancellation", "completed_stays", "family", "party_size", "no_request", "requests_per_guest", "arrival_weekend", "lead_bucket", "rel_price_lead", "adr_zero", "long_lead", "agent_recency_days", "agent_new", "agent_prior_365", "agent_avg_lead_365", "agent_lead_index", "agent_adr_index", "company_prior_rate", "company_prior_n", "country_prior_365", "country_share_365", "repeat_direct", "booking_days_to_month_end"]

# numeric feature columns
numeric_columns = numeric_columns_v1 + numeric_columns_v2

# party personas as a categorical feature
persona_labels = ["solo", "couple", "group of adults", "family with children", "family with baby"]

# minimum frequency for a category to keep its own level
min_category_count = 50


# add calendar, ratio and as of features to a bookings frame, as_of is the observation date per row
def add_derived(frame):
    out = frame.copy()
    out["arrival_date"] = pd.to_datetime(out["arrival_date"])
    out["booking_date"] = pd.to_datetime(out["booking_date"])
    if "as_of" not in out.columns:
        out["as_of"] = out["booking_date"]
    out["as_of"] = pd.to_datetime(out["as_of"])
    out["days_since_booking"] = (out["as_of"] - out["booking_date"]).dt.days.clip(lower=0)
    out["days_to_arrival"] = (out["arrival_date"] - out["as_of"]).dt.days
    out["survived_share"] = out["days_since_booking"] / out["lead_time"].clip(lower=1)
    out["arrival_month"] = out["arrival_date"].dt.month
    out["arrival_week"] = out["arrival_date"].dt.isocalendar().week.astype(int)
    out["arrival_dow"] = out["arrival_date"].dt.dayofweek
    out["booking_month"] = out["booking_date"].dt.month
    out["booking_dow"] = out["booking_date"].dt.dayofweek
    persons = (out["adults"] + out["children"]).clip(lower=1)
    out["adr_per_person"] = out["adr"] / persons
    out["has_company"] = (out["company"] != "NULL").astype(int)
    out["has_agent"] = (out["agent"] != "NULL").astype(int)
    out["is_portugal"] = (out["country"] == "PRT").astype(int)
    out["total_guests"] = out["adults"] + out["children"] + out["babies"]
    out["weekend_share"] = out["stays_in_weekend_nights"] / out["nights"].clip(lower=1)
    out["prev_cancel_ratio"] = out["previous_cancellations"] / (out["previous_cancellations"] + out["previous_bookings_not_canceled"]).clip(lower=1)
    out = add_guest_features(out)
    return out


# guest, party and account features of the second feature set
def add_guest_features(out):
    children = pd.to_numeric(out["children"], errors="coerce").fillna(0)
    babies = pd.to_numeric(out["babies"], errors="coerce").fillna(0)
    adults = pd.to_numeric(out["adults"], errors="coerce").fillna(0)
    out["has_prior_cancellation"] = (out["previous_cancellations"] > 0).astype(int)
    out["completed_stays"] = out["previous_bookings_not_canceled"]
    out["family"] = ((children + babies) > 0).astype(int)
    out["party_size"] = adults + children + babies
    out["persona"] = np.select([babies > 0, children > 0, adults >= 3, adults == 2, adults == 1], persona_labels[::-1], "other")
    out["no_request"] = (out["total_of_special_requests"] == 0).astype(int)
    out["requests_per_guest"] = out["total_of_special_requests"] / out["party_size"].clip(lower=1)
    out["arrival_weekend"] = out["arrival_date"].dt.dayofweek.isin([4, 5]).astype(int)
    out["lead_bucket"] = np.digitize(out["lead_time"], [1, 8, 31, 91, 181])
    out["adr_zero"] = (out["adr"] <= 0).astype(int)
    out["long_lead"] = (out["lead_time"] > 365).astype(int)
    for col in ["agent_recency_days", "agent_prior_365", "agent_avg_lead_365", "agent_avg_adr_365", "company_prior_rate", "company_prior_n", "country_prior_365", "hotel_prior_365"]:
        if col not in out.columns:
            out[col] = np.nan
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out["agent_new"] = (out["agent_recency_days"].isna() & (out["agent"] != "NULL")).astype(int)
    out["agent_recency_days"] = out["agent_recency_days"].fillna(400).clip(upper=400)
    out["agent_prior_365"] = out["agent_prior_365"].fillna(0)
    out["agent_avg_lead_365"] = out["agent_avg_lead_365"].fillna(out["lead_time"])
    out["agent_lead_index"] = out["lead_time"] / out["agent_avg_lead_365"].clip(lower=1)
    out["agent_adr_index"] = out["adr"] / out["agent_avg_adr_365"].fillna(out["adr"]).clip(lower=1)
    out["company_prior_rate"] = out["company_prior_rate"].fillna(0.37)
    out["company_prior_n"] = out["company_prior_n"].fillna(0)
    out["country_prior_365"] = out["country_prior_365"].fillna(0)
    out["country_share_365"] = out["country_prior_365"] / out["hotel_prior_365"].fillna(0).clip(lower=1)
    out["repeat_direct"] = ((out["is_repeated_guest"] == 1) & (out["market_segment"] == "Direct")).astype(int)
    out["booking_days_to_month_end"] = (out["booking_date"] + pd.offsets.MonthEnd(0) - out["booking_date"]).dt.days
    return out


# learn the category levels to keep and the rate medians per hotel, segment and arrival month from a training frame
def fit_levels(frame):
    levels = {}
    base = add_derived(frame)
    for col in categorical_columns:
        counts = base[col].astype(str).value_counts()
        levels[col] = sorted(counts[counts >= min_category_count].index.tolist())
    medians = base.groupby(["hotel", "market_segment", "arrival_month"])["adr"].median()
    levels["adr_medians"] = {"|".join(map(str, k)): float(v) for k, v in medians.items()}
    levels["adr_global_median"] = float(base["adr"].median())
    lead_medians = base.groupby(["hotel", "lead_bucket"])["adr"].median()
    levels["adr_lead_medians"] = {"|".join(map(str, k)): float(v) for k, v in lead_medians.items()}
    return levels


# rate relative to the median of the same hotel, segment and arrival month, and to the median of the same hotel and lead bucket, learned in training
def add_rel_price(out, levels):
    medians = levels.get("adr_medians", {})
    keys = out["hotel"].astype(str) + "|" + out["market_segment"].astype(str) + "|" + out["arrival_month"].astype(str)
    ref = keys.map(medians).fillna(levels.get("adr_global_median", 100.0)).clip(lower=1.0)
    out["rel_price"] = (out["adr"] / ref).clip(0, 10)
    lead_keys = out["hotel"].astype(str) + "|" + out["lead_bucket"].astype(str)
    lead_ref = lead_keys.map(levels.get("adr_lead_medians", {})).fillna(levels.get("adr_global_median", 100.0)).clip(lower=1.0)
    out["rel_price_lead"] = (out["adr"] / lead_ref).clip(0, 10)
    return out


# apply learned levels and return a feature matrix ready for lightgbm
def transform(frame, levels):
    out = add_derived(frame)
    out = add_rel_price(out, levels)
    for col in categorical_columns:
        keep = levels.get(col, [])
        values = out[col].astype(str)
        values = values.where(values.isin(keep), "other")
        out[col] = pd.Categorical(values, categories=keep + ["other"])
    for col in numeric_columns:
        if col not in out.columns:
            out[col] = 0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)
    return out[categorical_columns + numeric_columns]


# list of all feature names in matrix order
def feature_names():
    return categorical_columns + numeric_columns
