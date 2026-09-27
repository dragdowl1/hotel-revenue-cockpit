import datetime
import pandas as pd
from app import db
from app.feeds import common

BASE_URL = "https://api.frankfurter.dev/v1"
currencies = "USD,GBP,BRL,CHF,PLN,SEK,NOK,CNY,JPY,CAD,AUD"


# convert a frankfurter payload with a rates dict into rows
def rates_to_rows(payload):
    rows = []
    for date, rates in payload["rates"].items():
        for currency, rate in rates.items():
            rows.append({"date": datetime.date.fromisoformat(date), "base": "EUR", "currency": currency, "rate": rate, "fetched_at": common.now_utc()})
    return rows


# replace fx rows for the given dates
def upsert(rows):
    if len(rows) == 0:
        return 0
    frame = pd.DataFrame(rows)
    with db.db_lock:
        con = db.connect()
        try:
            con.register("incoming", frame)
            con.execute("delete from raw.fx_daily where date in (select date from incoming)")
            con.execute("insert into raw.fx_daily select * from incoming")
        finally:
            con.close()
    return len(frame)


# pull the latest ecb reference rates against the euro
def fetch_latest():
    payload = common.get_json(BASE_URL + "/latest", {"base": "EUR", "symbols": currencies})
    payload = {"rates": {payload["date"]: payload["rates"]}}
    return upsert(rates_to_rows(payload))


# backfill the fx history for the dataset period and the last two years if missing
def backfill():
    have = db.query_df("select count(*) as n from raw.fx_daily")["n"][0]
    if have > 500:
        return 0
    added = 0
    for start, end in [("2015-01-01", "2017-12-31"), (str(datetime.date.today() - datetime.timedelta(days=730)), str(datetime.date.today()))]:
        payload = common.get_json(BASE_URL + "/" + start + ".." + end, {"base": "EUR", "symbols": currencies})
        added = added + upsert(rates_to_rows(payload))
    return added


# backfill the daily rates from a start year in yearly chunks, years already present are skipped
def backfill_history(start_year=2014):
    have = db.query_df("select extract(year from date) as y, count(*) as n from raw.fx_daily group by 1")
    present = {int(y): int(n) for y, n in zip(have["y"], have["n"])}
    added = 0
    today = datetime.date.today()
    for year in range(start_year, today.year + 1):
        if present.get(year, 0) >= 200 * len(currencies.split(",")) and year < today.year:
            continue
        end = min(datetime.date(year, 12, 31), today)
        payload = common.get_json(BASE_URL + "/" + str(year) + "-01-01.." + str(end), {"base": "EUR", "symbols": currencies})
        added = added + upsert(rates_to_rows(payload))
    return added
