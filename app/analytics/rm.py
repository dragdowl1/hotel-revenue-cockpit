import datetime
import numpy as np
import pandas as pd
from app import db
from app.replay import clock

# rooms per hotel, the observed maximum of concurrent stays plus a small margin
capacity = {"City Hotel": 270, "Resort Hotel": 260}

# stay nights joined to their bookings, replay dates
stay_sql = "select s.hotel, cast(s.replay_stay_date as date) as stay_date, s.adr, s.is_canceled, s.market_segment, b.nights, b.replay_booking_date as booked, b.replay_leave_books_date as leaves, b.booking_id from staging.stg_stay_nights s join staging.stg_bookings b using (booking_id)"


# rooms and revenue on the books at as_of for stay dates in a window, per hotel and stay date
def otb_by_date(as_of, start, end):
    sql = "with x as (" + stay_sql + ") select hotel, stay_date, count(*) as rooms, round(sum(adr), 0) as revenue, count(*) filter (where nights = 1) as one_night_rooms, count(*) filter (where market_segment = 'Online TA') as ota_rooms, count(*) filter (where market_segment = 'Groups') as group_rooms from x where booked <= ? and leaves > ? and stay_date > ? and stay_date <= ? group by 1, 2 order by 1, 2"
    return db.query_df(sql, [as_of, as_of, start, end])


# realised rooms and revenue for stay dates in a window
def final_by_date(start, end):
    sql = "with x as (" + stay_sql + ") select hotel, stay_date, count(*) as rooms, round(sum(adr), 0) as revenue from x where is_canceled = 0 and stay_date > ? and stay_date <= ? group by 1, 2 order by 1, 2"
    return db.query_df(sql, [start, end])


# pickup ratios by weeks to arrival from last year, final rooms divided by rooms on the books that many weeks out, per hotel and calendar month of the stay so that a low season date is not judged by the high season booking curve; the pooled ratio per hotel is the fallback for thin months
def pickup_ratios(as_of, weeks=14, horizon=90):
    ly_start = as_of - datetime.timedelta(days=364 + 30)
    ly_end = as_of - datetime.timedelta(days=364 - horizon - 30)
    sql = ("with x as (" + stay_sql + "), buckets as (select unnest(range(0, ?)) as w) "
           "select x.hotel, extract(month from x.stay_date) as month, b.w as weeks_out, count(*) filter (where x.booked <= x.stay_date - to_days(7 * b.w) and x.leaves > x.stay_date - to_days(7 * b.w)) as otb, count(*) filter (where x.is_canceled = 0) as final "
           "from x cross join buckets b where x.stay_date > ? and x.stay_date <= ? group by 1, 2, 3 order by 1, 2, 3")
    frame = db.query_df(sql, [weeks, ly_start, ly_end])
    frame["month"] = frame["month"].astype(int)
    pooled = frame.groupby(["hotel", "weeks_out"], as_index=False)[["otb", "final"]].sum()
    pooled["pooled_ratio"] = (pooled["final"] / pooled["otb"].clip(lower=1)).clip(0.5, 6.0)
    frame = frame.merge(pooled[["hotel", "weeks_out", "pooled_ratio"]], on=["hotel", "weeks_out"])
    frame["ratio"] = (frame["final"] / frame["otb"].clip(lower=1)).clip(0.5, 6.0)
    thin = frame["final"] < 200
    frame.loc[thin, "ratio"] = frame.loc[thin, "pooled_ratio"]
    return frame


# forecast of final rooms per stay date from current on the books and last year's pickup ratios of the same calendar month
def forecast_by_date(as_of, horizon=90):
    otb = otb_by_date(as_of, as_of, as_of + datetime.timedelta(days=horizon))
    ratios = pickup_ratios(as_of)
    risk = db.query_df(
        "select b.hotel, cast(s.replay_stay_date as date) as stay_date, sum(o.p_cancel) as expected_cancels "
        "from scores.on_books o join staging.stg_bookings b using (booking_id) join staging.stg_stay_nights s using (booking_id) "
        "where o.as_of = (select max(as_of) from scores.on_books) and s.replay_stay_date > ? and s.replay_stay_date <= ? group by 1, 2", [as_of, as_of + datetime.timedelta(days=horizon)])
    rows = []
    for r in otb.to_dict("records"):
        weeks_out = min(int((pd.Timestamp(r["stay_date"]).date() - as_of).days // 7), 13)
        month = pd.Timestamp(r["stay_date"]).month
        ratio_row = ratios[(ratios["hotel"] == r["hotel"]) & (ratios["weeks_out"] == weeks_out) & (ratios["month"] == month)]
        if len(ratio_row) == 0:
            ratio_row = ratios[(ratios["hotel"] == r["hotel"]) & (ratios["weeks_out"] == weeks_out)].head(1).assign(ratio=lambda f: f["pooled_ratio"])
        ratio = float(ratio_row["ratio"].iloc[0]) if len(ratio_row) else 1.0
        rk = risk[(risk["hotel"] == r["hotel"]) & (pd.to_datetime(risk["stay_date"]).dt.date == pd.Timestamp(r["stay_date"]).date())]
        expected_cancels = float(rk["expected_cancels"].iloc[0]) if len(rk) else 0.0
        cap = capacity[r["hotel"]]
        forecast_rooms = min(r["rooms"] * ratio, cap)
        rows.append({"hotel": r["hotel"], "stay_date": str(r["stay_date"])[:10], "days_out": (pd.Timestamp(r["stay_date"]).date() - as_of).days, "rooms_otb": int(r["rooms"]), "revenue_otb": float(r["revenue"]), "adr_otb": round(float(r["revenue"]) / max(r["rooms"], 1), 1), "expected_cancels": round(expected_cancels, 1), "net_rooms": round(r["rooms"] - expected_cancels, 1), "pickup_ratio": round(ratio, 3), "forecast_rooms": round(forecast_rooms, 1), "forecast_occupancy": round(forecast_rooms / cap, 4), "otb_occupancy": round(r["rooms"] / cap, 4), "capacity": cap, "one_night_share": round(r["one_night_rooms"] / max(r["rooms"], 1), 3), "ota_share": round(r["ota_rooms"] / max(r["rooms"], 1), 3), "group_rooms": int(r["group_rooms"])})
    return pd.DataFrame(rows)


# headline numbers per hotel for a window of stay dates, with the same window one year earlier at the same days out
def brief(as_of, days=30):
    out = {}
    cur = otb_by_date(as_of, as_of, as_of + datetime.timedelta(days=days))
    ly_as_of = as_of - datetime.timedelta(days=364)
    ly = otb_by_date(ly_as_of, ly_as_of, ly_as_of + datetime.timedelta(days=days))
    ly_final = final_by_date(ly_as_of, ly_as_of + datetime.timedelta(days=days))
    pick = pickup(as_of, 7, days)
    fc = forecast_by_date(as_of, days)
    for hotel in capacity:
        c = cur[cur["hotel"] == hotel]; l = ly[ly["hotel"] == hotel]; f = ly_final[ly_final["hotel"] == hotel]; p = pick.get(hotel, {}); fh = fc[fc["hotel"] == hotel]
        cap_nights = capacity[hotel] * days
        out[hotel] = {
            "rooms_otb": int(c["rooms"].sum()), "revenue_otb": float(c["revenue"].sum()), "occupancy_otb": round(float(c["rooms"].sum()) / cap_nights, 4),
            "adr_otb": round(float(c["revenue"].sum()) / max(float(c["rooms"].sum()), 1), 1),
            "rooms_stly": int(l["rooms"].sum()), "revenue_stly": float(l["revenue"].sum()), "adr_stly": round(float(l["revenue"].sum()) / max(float(l["rooms"].sum()), 1), 1),
            "pace_index": round(float(c["rooms"].sum()) / max(float(l["rooms"].sum()), 1), 3),
            "rate_index": round((float(c["revenue"].sum()) / max(float(c["rooms"].sum()), 1)) / max(float(l["revenue"].sum()) / max(float(l["rooms"].sum()), 1), 1), 3),
            "rooms_final_ly": int(f["rooms"].sum()), "occupancy_final_ly": round(float(f["rooms"].sum()) / cap_nights, 4), "revenue_final_ly": float(f["revenue"].sum()),
            "forecast_rooms": round(float(fh["forecast_rooms"].sum()), 0), "forecast_occupancy": round(float(fh["forecast_rooms"].sum()) / cap_nights, 4), "forecast_revenue": round(float((fh["forecast_rooms"] * fh["adr_otb"]).sum()), 0),
            "expected_cancels": round(float(fh["expected_cancels"].sum()), 0), "net_rooms": round(float(fh["net_rooms"].sum()), 0),
            "pickup_gross": int(p.get("gross", 0)), "pickup_cancels": int(p.get("cancels", 0)), "biggest_cancel": p.get("biggest_cancel"), "pickup_net": int(p.get("gross", 0)) - int(p.get("cancels", 0)), "pickup_gross_stly": int(p.get("gross_stly", 0)), "pickup_cancels_stly": int(p.get("cancels_stly", 0)),
        }
    return out


# rooms picked up and rooms lost to cancellation over the last days for stay dates in the window, this year and last year
def pickup(as_of, back=7, days=30):
    out = {}
    for label, ref in [("", as_of), ("_stly", as_of - datetime.timedelta(days=364))]:
        start = ref - datetime.timedelta(days=back)
        sql = "with x as (" + stay_sql + ") select hotel, count(*) filter (where booked > ? and booked <= ?) as gross, count(*) filter (where leaves > ? and leaves <= ? and is_canceled = 1) as cancels from x where stay_date > ? and stay_date <= ? group by 1"
        frame = db.query_df(sql, [start, ref, start, ref, ref, ref + datetime.timedelta(days=days)])
        for r in frame.to_dict("records"):
            out.setdefault(r["hotel"], {})["gross" + label] = int(r["gross"])
            out[r["hotel"]]["cancels" + label] = int(r["cancels"])
        if label == "":
            biggest = db.query_df("with x as (" + stay_sql + ") select hotel, cast(leaves as date) as day, market_segment, count(distinct booking_id) as bookings, count(*) as nights from x where is_canceled = 1 and leaves > ? and leaves <= ? and stay_date > ? and stay_date <= ? group by 1, 2, 3 qualify row_number() over (partition by hotel order by count(*) desc) = 1", [start, ref, ref, ref + datetime.timedelta(days=days)])
            for r in biggest.to_dict("records"):
                out.setdefault(r["hotel"], {})["biggest_cancel"] = {"day": str(r["day"])[:10], "segment": r["market_segment"], "bookings": int(r["bookings"]), "nights": int(r["nights"])}
    return out


# daily pickup waterfall for the last weeks, gross bookings and cancellations per day for arrivals in the next 90 days
def pickup_daily(as_of, weeks=6, days=90):
    start = as_of - datetime.timedelta(weeks=weeks)
    sql = ("with x as (" + stay_sql + "), g as (select hotel, cast(booked as date) as day, count(*) as gross from x where booked > ? and booked <= ? and stay_date > booked and stay_date <= booked + to_days(?) group by 1, 2), "
           "c as (select hotel, cast(leaves as date) as day, count(*) as cancels from x where is_canceled = 1 and leaves > ? and leaves <= ? and stay_date > leaves and stay_date <= leaves + to_days(?) group by 1, 2) "
           "select coalesce(g.hotel, c.hotel) as hotel, coalesce(g.day, c.day) as day, coalesce(gross, 0) as gross, coalesce(cancels, 0) as cancels from g full outer join c on g.hotel = c.hotel and g.day = c.day order by 1, 2")
    return db.query_df(sql, [start, as_of, days, start, as_of, days])


# cumulative rooms on the books by days before arrival for a stay window, this year and last year, the pace curve
def pace_curve(as_of, days=30, max_out=120):
    out = {}
    for label, ref in [("this_year", as_of), ("last_year", as_of - datetime.timedelta(days=364))]:
        sql = ("with x as (" + stay_sql + "), d as (select unnest(range(0, ?, 5)) as days_out) "
               "select x.hotel, d.days_out, count(*) filter (where x.booked <= x.stay_date - to_days(d.days_out) and x.leaves > x.stay_date - to_days(d.days_out)) as rooms, count(distinct x.stay_date) as stay_dates "
               "from x cross join d where x.stay_date > ? and x.stay_date <= ? and x.stay_date - to_days(d.days_out) <= ? group by 1, 2 order by 1, 2")
        frame = db.query_df(sql, [max_out + 1, ref, ref + datetime.timedelta(days=days), ref])
        frame["rooms_per_date"] = (frame["rooms"] / frame["stay_dates"].clip(lower=1)).round(1)
        out[label] = frame.to_dict("records")
    return out


# on the books mix by market segment for the next days versus the same time last year
def channel_mix(as_of, days=90):
    out = {}
    for label, ref in [("this_year", as_of), ("last_year", as_of - datetime.timedelta(days=364))]:
        sql = "with x as (" + stay_sql + ") select hotel, market_segment, count(*) as rooms, round(avg(adr), 1) as adr from x where booked <= ? and leaves > ? and stay_date > ? and stay_date <= ? group by 1, 2 order by 1, 3 desc"
        out[label] = db.query_df(sql, [ref, ref, ref, ref + datetime.timedelta(days=days)]).to_dict("records")
    return out


# backtest of the booking curve forecast at weekly past origins, forecast rooms per stay date against realised rooms, with the rooms on the books as the naive reference
def forecast_backtest(as_of, origins=12, step_days=7, horizon=90):
    parts = []
    for k in range(1, origins + 1):
        origin = as_of - datetime.timedelta(days=step_days * k)
        end = min(origin + datetime.timedelta(days=horizon), as_of)
        otb = otb_by_date(origin, origin, end)
        if len(otb) == 0:
            continue
        final = final_by_date(origin, end).rename(columns={"rooms": "final"})[["hotel", "stay_date", "final"]]
        ratios = pickup_ratios(origin)[["hotel", "weeks_out", "ratio"]]
        merged = otb[["hotel", "stay_date", "rooms"]].merge(final, on=["hotel", "stay_date"], how="left")
        merged["final"] = merged["final"].fillna(0)
        merged["days_out"] = (pd.to_datetime(merged["stay_date"]) - pd.Timestamp(origin)).dt.days
        merged["weeks_out"] = (merged["days_out"] // 7).clip(upper=13)
        merged = merged.merge(ratios, on=["hotel", "weeks_out"], how="left")
        merged["ratio"] = merged["ratio"].fillna(1.0)
        merged["forecast"] = np.minimum(merged["rooms"] * merged["ratio"], merged["hotel"].map(capacity))
        merged["origin"] = str(origin)
        parts.append(merged)
    if len(parts) == 0:
        return {}
    frame = pd.concat(parts, ignore_index=True)
    out = {"origins": int(frame["origin"].nunique()), "step_days": step_days, "horizon_days": horizon, "stay_dates": int(len(frame)), "by_hotel": {}, "by_weeks_out": []}
    for hotel, part in list(frame.groupby("hotel")) + [("all", frame)]:
        out["by_hotel"][hotel] = error_metrics(part["final"].values, part["forecast"].values, part["rooms"].values)
    for (hotel, w), part in frame.groupby(["hotel", "weeks_out"]):
        m = error_metrics(part["final"].values, part["forecast"].values, part["rooms"].values)
        out["by_weeks_out"].append({"hotel": hotel, "weeks_out": int(w), "stay_dates": int(len(part)), "mae": m["mae"], "mae_otb": m["mae_otb"], "bias": m["bias"]})
    return out


# error metrics of a forecast against realised values, with the same metrics for the naive forecast
def error_metrics(actual, forecast, naive):
    actual = np.asarray(actual, dtype=float)
    forecast = np.asarray(forecast, dtype=float)
    naive = np.asarray(naive, dtype=float)
    mask = actual > 0
    out = {}
    out["rows"] = int(len(actual))
    out["mae"] = round(float(np.mean(np.abs(forecast - actual))), 2)
    out["rmse"] = round(float(np.sqrt(np.mean((forecast - actual) ** 2))), 2)
    out["mape"] = round(float(np.mean(np.abs(forecast[mask] - actual[mask]) / actual[mask])), 4) if mask.any() else None
    out["bias"] = round(float(np.mean(forecast - actual)), 2)
    out["mae_otb"] = round(float(np.mean(np.abs(naive - actual))), 2)
    out["rmse_otb"] = round(float(np.sqrt(np.mean((naive - actual) ** 2))), 2)
    out["mape_otb"] = round(float(np.mean(np.abs(naive[mask] - actual[mask]) / actual[mask])), 4) if mask.any() else None
    out["mean_final_rooms"] = round(float(np.mean(actual)), 1)
    return out
