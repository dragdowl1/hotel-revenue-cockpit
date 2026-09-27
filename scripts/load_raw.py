import os
import sys
import duckdb

sys.path.insert(0, os.getcwd())
from app import config

RAW_CSV = os.path.join(config.RAW_DIR, "hotel_bookings.csv")


# create the raw schema and load the hotel bookings csv with a stable booking id
def load_bookings(con):
    con.execute("create schema if not exists raw")
    con.execute("drop table if exists raw.bookings")
    con.execute(
        "create table raw.bookings as "
        "select row_number() over () as booking_id, * "
        "from read_csv('" + RAW_CSV + "', header=true, auto_detect=true, nullstr='NA', "
        "types={'children': 'DOUBLE', 'agent': 'VARCHAR', 'company': 'VARCHAR', 'reservation_status_date': 'DATE'})"
    )
    n = con.execute("select count(*) from raw.bookings").fetchone()[0]
    print("raw.bookings rows: " + str(n))


# create empty feed tables so dbt sources exist before the first feed pull
def create_feed_tables(con):
    con.execute("create table if not exists raw.weather_daily (location varchar, date date, temp_max double, temp_min double, precip_mm double, wind_max double, weather_code integer, kind varchar, fetched_at timestamp)")
    con.execute("create table if not exists raw.fx_daily (date date, base varchar, currency varchar, rate double, fetched_at timestamp)")
    con.execute("create table if not exists raw.holidays (country varchar, date date, name varchar, kind varchar, fetched_at timestamp)")
    con.execute("create table if not exists raw.pageviews_daily (project varchar, article varchar, date date, views bigint, fetched_at timestamp)")
    con.execute("create table if not exists raw.eurostat_nights (geo varchar, month date, nights double, fetched_at timestamp)")
    con.execute("create table if not exists raw.price_index (geo varchar, coicop varchar, month date, index double, fetched_at timestamp)")
    con.execute("create table if not exists raw.adr_regional (region varchar, typology varchar, month date, adr double, source varchar, fetched_at timestamp)")
    con.execute("create table if not exists raw.feed_runs (feed varchar, run_at timestamp, status varchar, rows_added integer, message varchar)")
    print("feed tables ready")


if __name__ == "__main__":
    os.makedirs(config.DATA_DIR, exist_ok=True)
    con = duckdb.connect(config.DB_PATH)
    load_bookings(con)
    create_feed_tables(con)
    con.close()
    print("database: " + config.DB_PATH)
