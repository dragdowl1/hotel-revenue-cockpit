import os
import re
import json
import gzip
import datetime
import httpx
from app import config
from app import db
from app.feeds import common

PAGE_URL = "https://insideairbnb.com/get-the-data/"
STATE_PATH = os.path.join(config.DATA_DIR, "airbnb_snapshot.json")
LISTINGS_GZ = os.path.join(config.RAW_DIR, "airbnb_lisbon_listings.csv.gz")
CALENDAR_GZ = os.path.join(config.RAW_DIR, "airbnb_lisbon_calendar.csv.gz")
REVIEWS_GZ = os.path.join(config.RAW_DIR, "airbnb_lisbon_reviews.csv.gz")
SNAPSHOT_DATE = "2026-06-23"
files = {"listings": LISTINGS_GZ, "calendar": CALENDAR_GZ, "reviews": REVIEWS_GZ}

listing_columns = "id, name, host_id, host_since, host_is_superhost, host_listings_count, neighbourhood_cleansed, neighbourhood_group_cleansed, latitude, longitude, property_type, room_type, accommodates, bathrooms, bedrooms, beds, amenities, price, minimum_nights, maximum_nights, availability_30, availability_60, availability_90, availability_365, number_of_reviews, number_of_reviews_ltm, first_review, last_review, review_scores_rating, review_scores_cleanliness, review_scores_location, review_scores_value, instant_bookable, reviews_per_month"


# current snapshot date from the state file or the built in default
def snapshot_date():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f).get("snapshot_date", SNAPSHOT_DATE)
    return SNAPSHOT_DATE


# latest lisbon snapshot date advertised on the inside airbnb page
def latest_snapshot():
    with httpx.Client(headers={"User-Agent": config.user_agent}, timeout=30.0, follow_redirects=True) as c:
        page = c.get(PAGE_URL)
        page.raise_for_status()
    dates = re.findall(r"data\.insideairbnb\.com/portugal/lisbon/lisbon/(\d{4}-\d{2}-\d{2})/data/listings\.csv\.gz", page.text)
    if not dates:
        raise RuntimeError("no lisbon snapshot links found on the page")
    return max(dates)


# download one file to a temporary path and check that it is readable gzip csv
def download(url, target):
    tmp = target.replace(".csv.gz", ".new.csv.gz")
    with httpx.Client(headers={"User-Agent": config.user_agent}, timeout=120.0, follow_redirects=True) as c:
        with c.stream("GET", url) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
    with gzip.open(tmp, "rt", encoding="utf-8", errors="replace") as f:
        header = f.readline()
    if "," not in header or len(header) < 10:
        raise RuntimeError("downloaded file has no csv header: " + url)
    return tmp


# load the three files into new tables, validate row counts against the current tables, then swap
def load_from(paths, con):
    con.execute("create schema if not exists raw")
    con.execute("drop table if exists raw.airbnb_listings_new")
    con.execute("create table raw.airbnb_listings_new as select " + listing_columns + ", try_cast(replace(replace(price, '$', ''), ',', '') as double) as price_num from read_csv('" + paths["listings"] + "', header=true, all_varchar=true)")
    con.execute("drop table if exists raw.airbnb_calendar_new")
    con.execute("create table raw.airbnb_calendar_new as select cast(listing_id as bigint) as listing_id, cast(date as date) as date, available = 't' as available, try_cast(minimum_nights as integer) as minimum_nights from read_csv('" + paths["calendar"] + "', header=true, all_varchar=true)")
    con.execute("drop table if exists raw.airbnb_reviews_new")
    con.execute("create table raw.airbnb_reviews_new as select cast(listing_id as bigint) as listing_id, cast(id as bigint) as review_id, cast(date as date) as date, comments from read_csv('" + paths["reviews"] + "', header=true, all_varchar=true, quote='\"', escape='\"') where date >= '2023-01-01' and length(comments) > 40")
    counts = {}
    for name in ["listings", "calendar", "reviews"]:
        new = con.execute("select count(*) from raw.airbnb_" + name + "_new").fetchone()[0]
        old = con.execute("select count(*) from raw.airbnb_" + name).fetchone()[0] if con.execute("select count(*) from information_schema.tables where table_schema = 'raw' and table_name = 'airbnb_" + name + "'").fetchone()[0] else 0
        if new < old * 0.5:
            raise RuntimeError("new " + name + " table has " + str(new) + " rows against " + str(old) + " before, refusing to swap")
        counts[name] = new
    for name in ["listings", "calendar", "reviews"]:
        con.execute("drop table if exists raw.airbnb_" + name)
        con.execute("alter table raw.airbnb_" + name + "_new rename to airbnb_" + name)
    return counts


# load the files currently on disk, used for the first load
def load():
    with db.db_lock:
        con = db.connect()
        try:
            counts = load_from(files, con)
        finally:
            con.close()
    print("airbnb listings " + str(counts["listings"]) + ", calendar rows " + str(counts["calendar"]) + ", reviews since 2023 " + str(counts["reviews"]))
    return counts["listings"]


# check for a newer snapshot and replace the data only when every step succeeds
def refresh(force=False):
    current = snapshot_date()
    latest = latest_snapshot()
    if latest <= current and not force:
        print("airbnb snapshot " + current + " is the latest")
        return 0
    base = "https://data.insideairbnb.com/portugal/lisbon/lisbon/" + latest + "/data/"
    tmp_paths = {}
    try:
        for name, target in files.items():
            tmp_paths[name] = download(base + name + ".csv.gz", target)
        with db.db_lock:
            con = db.connect()
            try:
                counts = load_from(tmp_paths, con)
            finally:
                con.close()
        for name, target in files.items():
            os.replace(tmp_paths[name], target)
        with open(STATE_PATH, "w") as f:
            json.dump({"snapshot_date": latest, "loaded_at": common.now_utc().isoformat(), "counts": counts}, f)
        print("airbnb snapshot updated to " + latest)
        return counts["listings"]
    except Exception:
        for p in tmp_paths.values():
            if os.path.exists(p):
                os.remove(p)
        with db.db_lock:
            con = db.connect()
            try:
                for name in ["listings", "calendar", "reviews"]:
                    con.execute("drop table if exists raw.airbnb_" + name + "_new")
            finally:
                con.close()
        raise
