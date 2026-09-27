import os
from app import config
from app import db


# export the staged bookings made up to today for the notebooks, outcomes not revealed yet are masked so nothing from the future leaks into training
def export_bookings():
    os.makedirs(config.PARQUET_DIR, exist_ok=True)
    path = os.path.join(config.PARQUET_DIR, "bookings.parquet")
    db.execute("copy (select * exclude (is_canceled, reservation_status, reservation_status_date, leave_books_date, replay_leave_books_date), "
               "case when outcome_known then is_canceled else null end as is_canceled, "
               "case when outcome_known then reservation_status else 'Open' end as reservation_status, "
               "case when outcome_known then reservation_status_date else null end as reservation_status_date, "
               "case when outcome_known then leave_books_date else date '2099-12-31' end as leave_books_date, "
               "case when outcome_known then replay_leave_books_date else date '2099-12-31' end as replay_leave_books_date "
               "from staging.stg_bookings where replay_booking_date <= current_date) to '" + path + "' (format parquet)")
    return path


# export the live feed tables to parquet for notebooks, tables not loaded yet are skipped
def export_feeds():
    os.makedirs(config.PARQUET_DIR, exist_ok=True)
    for name, sql in [("pageviews", "select * from raw.pageviews_daily"), ("eurostat", "select * from raw.eurostat_nights"), ("adr_regional", "select * from raw.adr_regional"), ("weather", "select * from raw.weather_daily"), ("holidays", "select * from raw.holidays"), ("fx", "select * from raw.fx_daily"), ("airbnb_listings", "select * from raw.airbnb_listings"), ("airbnb_availability", "select listing_id, count(*) filter (where not available) as booked_days, count(*) as days from raw.airbnb_calendar group by 1"), ("airbnb_reviews_sample", "select listing_id, review_id, date, comments from raw.airbnb_reviews where date >= '2025-01-01' using sample 150000 rows (reservoir, 7)")]:
        table = sql.split("from raw.")[1].split()[0]
        if not table_exists("raw", table):
            print("export of " + name + " skipped, table raw." + table + " not loaded yet")
            continue
        path = os.path.join(config.PARQUET_DIR, name + ".parquet")
        db.execute("copy (" + sql + ") to '" + path + "' (format parquet)")
    return config.PARQUET_DIR


# true when a table exists in the database
def table_exists(schema, name):
    frame = db.query_df("select count(*) as n from information_schema.tables where table_schema = ? and table_name = ?", [schema, name])
    return int(frame["n"][0]) > 0


if __name__ == "__main__":
    print("exported " + export_bookings())
    print("exported feeds to " + export_feeds())
