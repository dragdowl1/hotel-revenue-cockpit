import datetime
import pandas as pd
from app import db
from app.feeds import common

# monthly nights spent at tourist accommodation, hotels and similar, by country
DATA_URL = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/tour_occ_nim"
REGION_URL = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/tour_occ_nin2m"
PRICE_URL = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_midx"
geos = ["PT", "ES", "IT", "FR", "EL", "HR"]

# nuts 2 regions of the two hotels, lisbon metropolitan area and the algarve, monthly nights published from 2020
regions = ["PT17", "PT15"]

# harmonised consumer price index of accommodation services in portugal, 2015 = 100
price_coicop = "CP112"


# decode a json stat payload with one free dimension time into rows
def decode(payload, geo):
    dims = payload["dimension"]
    time_index = dims["time"]["category"]["index"]
    values = payload["value"]
    ids = payload["id"]
    sizes = payload["size"]
    time_pos = ids.index("time")
    stride = 1
    for i in range(time_pos + 1, len(ids)):
        stride = stride * sizes[i]
    rows = []
    for label, idx in time_index.items():
        key = str(idx * stride)
        if key not in values:
            continue
        parts = label.replace("M", "-").split("-")
        month = datetime.date(int(parts[0]), int(parts[1]), 1)
        rows.append({"geo": geo, "month": month, "nights": float(values[key]), "fetched_at": common.now_utc()})
    return rows


# replace eurostat rows for one country
def upsert(rows, geo):
    if len(rows) == 0:
        return 0
    frame = pd.DataFrame(rows)
    with db.db_lock:
        con = db.connect()
        try:
            con.register("incoming", frame)
            con.execute("delete from raw.eurostat_nights where geo = ? and month in (select month from incoming)", [geo])
            con.execute("insert into raw.eurostat_nights select * from incoming")
        finally:
            con.close()
    return len(frame)


# pull the whole monthly series for every country
def refresh():
    added = 0
    for geo in geos:
        params = {"format": "JSON", "lang": "EN", "geo": geo, "unit": "NR", "c_resid": "TOTAL", "nace_r2": "I551", "sinceTimePeriod": "2014-01"}
        try:
            payload = common.get_json(DATA_URL, params)
            added = added + upsert(decode(payload, geo), geo)
        except Exception as e:
            print("eurostat " + geo + " skipped, " + str(e))
    return added


# decode the regional payload, annual time dimension with a month dimension, into monthly rows
def decode_regional(payload, geo):
    dims = payload["dimension"]
    ids = payload["id"]
    sizes = payload["size"]
    values = payload["value"]
    month_index = dims["month"]["category"]["index"]
    time_index = dims["time"]["category"]["index"]
    strides = {}
    stride = 1
    for i in range(len(ids) - 1, -1, -1):
        strides[ids[i]] = stride
        stride = stride * sizes[i]
    rows = []
    for year, ti in time_index.items():
        for m, mi in month_index.items():
            if m == "TOTAL":
                continue
            key = str(ti * strides["time"] + mi * strides["month"])
            if key not in values:
                continue
            rows.append({"geo": geo, "month": datetime.date(int(year), int(m[1:]), 1), "nights": float(values[key]), "fetched_at": common.now_utc()})
    return rows


# pull the monthly nights of the two hotel regions, hotels and similar accommodation
def refresh_regions():
    added = 0
    for geo in regions:
        params = {"format": "JSON", "lang": "EN", "geo": geo, "unit": "NR", "c_resid": "TOTAL", "nace_r2": "I551-I553", "sinceTimePeriod": "2014"}
        try:
            payload = common.get_json(REGION_URL, params)
            added = added + upsert(decode_regional(payload, geo), geo)
        except Exception as e:
            print("eurostat region " + geo + " skipped, " + str(e))
    return added


# pull the accommodation price index for portugal into raw.price_index
def refresh_prices():
    params = {"format": "JSON", "lang": "EN", "geo": "PT", "coicop": price_coicop, "unit": "I15", "sinceTimePeriod": "2014-01"}
    payload = common.get_json(PRICE_URL, params)
    rows = decode(payload, "PT")
    if len(rows) == 0:
        return 0
    frame = pd.DataFrame(rows).rename(columns={"nights": "index"})
    frame["coicop"] = price_coicop
    frame = frame[["geo", "coicop", "month", "index", "fetched_at"]]
    with db.db_lock:
        con = db.connect()
        try:
            con.execute("create table if not exists raw.price_index (geo varchar, coicop varchar, month date, index double, fetched_at timestamp)")
            con.register("incoming", frame)
            con.execute("delete from raw.price_index where geo = 'PT' and coicop = ? and month in (select month from incoming)", [price_coicop])
            con.execute("insert into raw.price_index select * from incoming")
        finally:
            con.close()
    return len(frame)
