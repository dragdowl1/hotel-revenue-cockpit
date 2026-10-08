import sys
import datetime
import numpy as np
import pandas as pd
from app import db
from app.synthetic import generator

# first arrival week of the synthetic history and the horizon of arrivals generated beyond today
history_start = "2014-01-06"
horizon_days = 400
seed = 7


# write the synthetic bookings table, replacing the previous content
def write_table(frame, log=True):
    with db.db_lock:
        con = db.connect()
        try:
            con.execute("create schema if not exists synthetic")
            con.register("incoming", frame)
            con.execute("create or replace table synthetic.bookings as select * from incoming")
            if log:
                con.execute("create table if not exists synthetic.generation_log (run_at timestamp, rows bigint, first_arrival_year integer, last_arrival_year integer, seed integer)")
                con.execute("insert into synthetic.generation_log values (?, ?, ?, ?, ?)", [datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None), int(len(frame)), int(frame["arrival_date_year"].min()), int(frame["arrival_date_year"].max()), int(seed)])
        finally:
            con.close()


# monthly summary of a generated frame per hotel, gross bookings, cancel rate, mean lead and mean rate
def summary(frame):
    month = pd.to_datetime(frame["arrival_date_year"].astype(str) + "-" + frame["arrival_date_month"].str[:3] + "-01", format="%Y-%b-%d")
    out = frame.assign(month=month).groupby(["hotel", "month"]).agg(bookings=("booking_id", "size"), cancel_rate=("is_canceled", "mean"), lead=("lead_time", "mean"), adr=("adr", "mean"), shocked=("shock", lambda s: int((s != "").sum())))
    return out.round(3)


# generate the whole history from the start week to the horizon and store it
def full_run(until=None):
    today = datetime.date.today()
    end = pd.Timestamp(today + datetime.timedelta(days=horizon_days)) if until is None else pd.Timestamp(until)
    world = generator.World(end + pd.DateOffset(months=2))
    frame, targets = generator.generate(world, history_start, end, seed)
    write_table(frame)
    log_batch("full", pd.Timestamp(history_start).date(), today, len(frame), 0)
    print("synthetic bookings " + str(len(frame)) + ", arrivals from " + history_start + " to " + str(end.date()))
    return frame, targets


# the stored synthetic bookings with typed dates
def load_existing():
    frame = db.query_df("select * from synthetic.bookings")
    month_number = {name: i + 1 for i, name in enumerate(generator.pool_module.month_names)}
    frame["arrival_date"] = pd.to_datetime(pd.DataFrame({"year": frame["arrival_date_year"], "month": frame["arrival_date_month"].map(month_number), "day": frame["arrival_date_day_of_month"]}))
    frame["booking_date"] = frame["arrival_date"] - pd.to_timedelta(frame["lead_time"], unit="D")
    frame["nights"] = frame["stays_in_weekend_nights"] + frame["stays_in_week_nights"]
    return frame


# record one batch in the generation log
def log_batch(kind, batch_from, batch_to, generated, kept, flipped=0):
    with db.db_lock:
        con = db.connect()
        try:
            con.execute("create table if not exists synthetic.batches (run_at timestamp, kind varchar, batch_from date, batch_to date, generated bigint, kept bigint, flipped bigint)")
            con.execute("insert into synthetic.batches values (?, ?, ?, ?, ?, ?, ?)", [datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None), kind, batch_from, batch_to, int(generated), int(kept), int(flipped)])
        finally:
            con.close()


# daily hazard on bookings already made whose outcome is not revealed yet: events active in the batch window spread over their duration, a storm or heat day in the forecast for the arrival checked once three days out, no shows on disruption days
def daily_outcomes(world, frame, batch_from, today):
    leave = pd.to_datetime(frame["reservation_status_date"])
    open_mask = (frame["booking_date"] <= today) & (leave > today) & (frame["is_canceled"] == 0)
    if not open_mask.any():
        return frame, 0
    before = int(frame["is_canceled"].sum())
    days = pd.date_range(batch_from + pd.Timedelta(days=1), today, freq="D")
    for day in days:
        rng = np.random.default_rng(int(day.strftime("%Y%m%d")) * 7 + 3)
        checkout = frame["arrival_date"] + pd.to_timedelta(frame["nights"], unit="D")
        alive = open_mask & (frame["is_canceled"] == 0) & (frame["arrival_date"] >= day)
        for _, event in world.events.iterrows():
            if event["cancel_flip_share"] <= 0 and event["noshow_share"] <= 0:
                continue
            if day < event["start_date"] or day > event["end_date"]:
                continue
            duration = max((event["end_date"] - event["start_date"]).days + 1, 1)
            daily_share = 1.0 - (1.0 - float(event["cancel_flip_share"])) ** (1.0 / duration)
            hit = alive & (frame["arrival_date"] <= event["end_date"]) & (checkout > event["start_date"])
            if event["region"] != "both":
                hit = hit & (frame["hotel"].map(generator.pool_module.hotel_region) == event["region"])
            if len(event["markets"]) > 0:
                hit = hit & frame["country"].isin(event["markets"])
            if len(event["segment_filter"]) > 0:
                hit = hit & frame["market_segment"].isin(event["segment_filter"])
            rows = frame.index[hit]
            if len(rows) == 0:
                continue
            flex = frame.loc[rows, "deposit_type"].map(generator.flexibility).fillna(0.5).values
            flip = rng.random(len(rows)) < daily_share * flex
            flipped = rows[flip]
            frame.loc[flipped, "is_canceled"] = 1
            frame.loc[flipped, "reservation_status"] = "Canceled"
            frame.loc[flipped, "reservation_status_date"] = day
            frame.loc[flipped, "shock"] = event["event_id"]
            arriving = frame.index[alive & (frame["arrival_date"] == day) & ~frame.index.isin(flipped)]
            if event["noshow_share"] > 0 and len(arriving) > 0:
                if event["region"] != "both":
                    arriving = arriving[frame.loc[arriving, "hotel"].map(generator.pool_module.hotel_region) == event["region"]]
                hit_ns = rng.random(len(arriving)) < float(event["noshow_share"])
                rows_ns = arriving[hit_ns]
                frame.loc[rows_ns, "is_canceled"] = 1
                frame.loc[rows_ns, "reservation_status"] = "No-Show"
                frame.loc[rows_ns, "reservation_status_date"] = day
                frame.loc[rows_ns, "shock"] = event["event_id"]
        three_out = alive & (frame["arrival_date"] == day + pd.Timedelta(days=3)) & (frame["deposit_type"] == "No Deposit") & (frame["is_canceled"] == 0)
        if three_out.any():
            rows = frame.index[three_out]
            location = frame.loc[rows, "hotel"].map(generator.pool_module.hotel_region)
            flags = world.weather.reindex(list(zip(location, frame.loc[rows, "arrival_date"])))["storm"].fillna(0).values == 1
            hit = flags & (rng.random(len(rows)) < generator.storm_flip_share)
            flipped = rows[hit]
            frame.loc[flipped, "is_canceled"] = 1
            frame.loc[flipped, "reservation_status"] = "Canceled"
            frame.loc[flipped, "reservation_status_date"] = day
            frame.loc[flipped, "shock"] = "storm"
    return frame, int(frame["is_canceled"].sum()) - before


# daily batch: emit the bookings made since the last batch up to today for every arrival week in reach, bookings already made stay frozen, the rest is redrawn from today's signals
def daily_batch():
    today = pd.Timestamp(datetime.date.today())
    existing = load_existing()
    batch_from = last_batch_end(existing)
    if batch_from >= today:
        print("synthetic batch up to date, last booking date " + str(batch_from.date()))
        return 0
    window_start = (batch_from - pd.Timedelta(days=batch_from.dayofweek)) - pd.Timedelta(weeks=1)
    end = today + pd.Timedelta(days=horizon_days)
    world = generator.World(end + pd.DateOffset(months=2))
    weeks = pd.date_range(window_start, end, freq="W-MON")
    targets = generator.weekly_targets(world, weeks)
    frozen = existing[existing["booking_date"] <= batch_from]
    frozen_week = frozen.assign(week_start=frozen["arrival_date"] - pd.to_timedelta(frozen["arrival_date"].dt.dayofweek, unit="D"))
    frozen_counts = frozen_week.groupby(["hotel", "week_start"]).size()
    parts = []
    for row in targets.itertuples(index=False):
        have = int(frozen_counts.get((row.hotel, row.week_start), 0))
        part = generator.generate_week(world, row.hotel, row.week_start, max(row.target - have, 0.0), seed + int(today.strftime("%Y%m%d")))
        if part is not None:
            parts.append(part[part["booking_date"] > batch_from])
    new = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=existing.columns)
    keep = existing[(existing["booking_date"] <= batch_from) | (existing["arrival_date"] < window_start)]
    if len(new) > 0:
        new = new.sort_values(["booking_date", "arrival_date", "hotel"]).reset_index(drop=True)
        new["booking_id"] = np.arange(1, len(new) + 1) + int(existing["booking_id"].max())
        new["arrival_date_year"] = new["arrival_date"].dt.year
        new["arrival_date_month"] = new["arrival_date"].dt.month.map(lambda m: generator.pool_module.month_names[m - 1])
        new["arrival_date_week_number"] = new["arrival_date"].dt.isocalendar().week.astype(int)
        new["arrival_date_day_of_month"] = new["arrival_date"].dt.day
        new["generated_at"] = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    union = pd.concat([keep, new[keep.columns]], ignore_index=True) if len(new) > 0 else keep
    union, refused = generator.accept_bookings(world, union, protected=(union["booking_date"] <= batch_from).values)
    print("bookings turned away by the stop sell rule: " + ", ".join(h + " " + str(n) for h, n in refused.items()))
    union, flipped = daily_outcomes(world, union, batch_from, today)
    union = generator.enforce_capacity(union)
    write_table(union[generator.output_columns()], log=False)
    emitted = int(((new["booking_date"] > batch_from) & (new["booking_date"] <= today)).sum()) if len(new) > 0 else 0
    log_batch("daily", batch_from.date(), today.date(), emitted, len(keep), flipped)
    print("synthetic batch from " + str(batch_from.date()) + " to " + str(today.date()) + ", bookings made " + str(emitted) + ", not yet made redrawn " + str(len(new) - emitted) + ", open outcomes flipped by the daily hazard " + str(flipped))
    return emitted


# end date of the last logged batch, the last generation date of the stored bookings when no batch was logged yet
def last_batch_end(existing):
    if table_exists("synthetic", "batches"):
        frame = db.query_df("select max(batch_to) as d from synthetic.batches")
        if not pd.isna(frame["d"][0]):
            return pd.Timestamp(frame["d"][0])
    return pd.Timestamp(existing["generated_at"].max()).normalize()


# the weekly refresh is the daily batch
def refresh():
    return daily_batch()


# true when the last batch ended before yesterday
def batch_overdue():
    today = pd.Timestamp(datetime.date.today())
    frame = db.query_df("select max(batch_to) as d from synthetic.batches") if table_exists("synthetic", "batches") else None
    if frame is None or pd.isna(frame["d"][0]):
        return True
    return pd.Timestamp(frame["d"][0]) < today - pd.Timedelta(days=1)


# true when a table exists
def table_exists(schema, name):
    frame = db.query_df("select count(*) as n from information_schema.tables where table_schema = ? and table_name = ?", [schema, name])
    return int(frame["n"][0]) > 0


if __name__ == "__main__":
    until = sys.argv[1] if len(sys.argv) > 1 else None
    frame, targets = full_run(until)
    print(summary(frame).to_string())
