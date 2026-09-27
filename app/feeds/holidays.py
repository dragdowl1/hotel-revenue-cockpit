import datetime
import pandas as pd
from app import db
from app.feeds import common

NAGER_URL = "https://date.nager.at/api/v3/PublicHolidays"
OPENHOLIDAYS_URL = "https://openholidaysapi.org/SchoolHolidays"

# top booker countries of the dataset plus portugal itself
public_countries = ["PT", "GB", "FR", "ES", "DE", "IT", "IE", "BE", "BR", "NL", "US", "CH", "CN", "AT", "SE"]
school_countries = ["PT", "GB", "FR", "ES", "DE", "IT", "NL", "BE", "AT", "CH"]


# replace holiday rows for one country and kind
def upsert(rows, country, kind):
    if len(rows) == 0:
        return 0
    frame = pd.DataFrame(rows)
    with db.db_lock:
        con = db.connect()
        try:
            con.register("incoming", frame)
            con.execute("delete from raw.holidays where country = ? and kind = ? and date in (select date from incoming)", [country, kind])
            con.execute("insert into raw.holidays select * from incoming")
        finally:
            con.close()
    return len(frame)


# pull public holidays for one country and year from nager date
def fetch_public(country, year):
    payload = common.get_json(NAGER_URL + "/" + str(year) + "/" + country)
    rows = []
    for h in payload:
        if not h.get("global", True):
            continue
        rows.append({"country": country, "date": datetime.date.fromisoformat(h["date"]), "name": h["localName"], "kind": "public", "fetched_at": common.now_utc()})
    return upsert(rows, country, "public")


# pull school holidays for one country and date range from openholidays, expanded to days
def fetch_school(country, start, end):
    params = {"countryIsoCode": country, "validFrom": str(start), "validTo": str(end), "languageIsoCode": "EN"}
    payload = common.get_json(OPENHOLIDAYS_URL, params)
    rows = []
    for h in payload:
        name = h["name"][0]["text"] if h.get("name") else "school holiday"
        d0 = datetime.date.fromisoformat(h["startDate"])
        d1 = datetime.date.fromisoformat(h["endDate"])
        day = d0
        while day <= d1:
            rows.append({"country": country, "date": day, "name": name, "kind": "school", "fetched_at": common.now_utc()})
            day = day + datetime.timedelta(days=1)
    frame = pd.DataFrame(rows)
    if len(frame) == 0:
        return 0
    frame = frame.drop_duplicates(subset=["country", "date"])
    return upsert(frame.to_dict("records"), country, "school")


# load holidays for all years needed by the dataset and the replay window
def backfill():
    have = db.query_df("select count(*) as n from raw.holidays")["n"][0]
    if have > 1000:
        return 0
    added = 0
    years = [2015, 2016, 2017, datetime.date.today().year - 1, datetime.date.today().year, datetime.date.today().year + 1]
    for country in public_countries:
        for year in years:
            try:
                added = added + fetch_public(country, year)
            except Exception as e:
                print("holidays public " + country + " " + str(year) + " skipped, " + str(e))
    for country in school_countries:
        for start, end in [(datetime.date(2015, 1, 1), datetime.date(2017, 12, 31)), (datetime.date(datetime.date.today().year - 1, 1, 1), datetime.date(datetime.date.today().year + 1, 12, 31))]:
            try:
                added = added + fetch_school(country, start, end)
            except Exception as e:
                print("holidays school " + country + " skipped, " + str(e))
    return added


# refresh the current and next year public holidays
def refresh():
    added = 0
    for country in public_countries:
        for year in [datetime.date.today().year, datetime.date.today().year + 1]:
            try:
                added = added + fetch_public(country, year)
            except Exception as e:
                print("holidays public " + country + " " + str(year) + " skipped, " + str(e))
    return added


# backfill public and school holidays for every year from a start year, years already present are skipped
def backfill_history(start_year=2014):
    have = db.query_df("select country, kind, extract(year from date) as y, count(*) as n from raw.holidays group by 1, 2, 3")
    present = set(zip(have["country"], have["kind"], have["y"].astype(int)))
    added = 0
    last_year = datetime.date.today().year + 1
    for country in public_countries:
        for year in range(start_year, last_year + 1):
            if (country, "public", year) in present:
                continue
            try:
                added = added + fetch_public(country, year)
            except Exception as e:
                print("holidays public " + country + " " + str(year) + " skipped, " + str(e))
    for country in school_countries:
        for year in range(start_year, last_year + 1):
            if (country, "school", year) in present:
                continue
            try:
                added = added + fetch_school(country, datetime.date(year, 1, 1), datetime.date(year, 12, 31))
            except Exception as e:
                print("holidays school " + country + " " + str(year) + " skipped, " + str(e))
    return added
