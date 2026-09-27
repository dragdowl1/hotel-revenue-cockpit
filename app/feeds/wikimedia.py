import datetime
import time
import pandas as pd
from app import db
from app.feeds import common

BASE_URL = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"

# lisbon article per language edition, language stands for the source market
articles = {
    "en.wikipedia": "Lisbon",
    "de.wikipedia": "Lissabon",
    "fr.wikipedia": "Lisbonne",
    "es.wikipedia": "Lisboa",
    "it.wikipedia": "Lisbona",
    "pt.wikipedia": "Lisboa",
    "nl.wikipedia": "Lissabon",
    "pl.wikipedia": "Lizbona",
    "sv.wikipedia": "Lissabon",
    "ja.wikipedia": "リスボン",
    "zh.wikipedia": "里斯本",
    "ru.wikipedia": "Лиссабон",
}


# replace pageview rows for one project and article
def upsert(rows, project, article):
    if len(rows) == 0:
        return 0
    frame = pd.DataFrame(rows)
    with db.db_lock:
        con = db.connect()
        try:
            con.register("incoming", frame)
            con.execute("delete from raw.pageviews_daily where project = ? and article = ? and date in (select date from incoming)", [project, article])
            con.execute("insert into raw.pageviews_daily select * from incoming")
        finally:
            con.close()
    return len(frame)


# pull daily user pageviews for one article between two dates
def fetch_article(project, article, start, end):
    url = BASE_URL + "/" + project + "/all-access/user/" + article + "/daily/" + start.strftime("%Y%m%d") + "/" + end.strftime("%Y%m%d")
    payload = common.get_json(url)
    rows = []
    for item in payload.get("items", []):
        rows.append({"project": project, "article": article, "date": datetime.datetime.strptime(item["timestamp"][:8], "%Y%m%d").date(), "views": item["views"], "fetched_at": common.now_utc()})
    return upsert(rows, project, article)


# pull the last days for every language edition, polite one second spacing
def refresh(days=10):
    end = datetime.date.today() - datetime.timedelta(days=1)
    start = end - datetime.timedelta(days=days)
    added = 0
    for project, article in articles.items():
        try:
            added = added + fetch_article(project, article, start, end)
        except Exception as e:
            print("pageviews " + project + " skipped, " + str(e))
        time.sleep(1.0)
    return added


# missing date ranges for one language between the first date wanted and two days ago
def missing_ranges(project, first, end):
    have = db.query_df("select date from raw.pageviews_daily where project = ? order by date", [project])["date"]
    present = set(pd.to_datetime(have).dt.date.tolist())
    ranges = []
    day = first
    open_start = None
    while day <= end:
        if day not in present and open_start is None:
            open_start = day
        if day in present and open_start is not None:
            ranges.append((open_start, day - datetime.timedelta(days=1)))
            open_start = None
        day = day + datetime.timedelta(days=1)
    if open_start is not None:
        ranges.append((open_start, end))
    return ranges


# fill missing ranges per language, a few ranges per run so the api is not hammered
def backfill(max_ranges=6):
    end = datetime.date.today() - datetime.timedelta(days=2)
    added = 0
    for project, article in articles.items():
        for start, stop in missing_ranges(project, datetime.date(2015, 7, 1), end)[:max_ranges]:
            chunk_start = start
            while chunk_start <= stop:
                chunk_end = min(chunk_start + datetime.timedelta(days=365), stop)
                try:
                    added = added + fetch_article(project, article, chunk_start, chunk_end)
                except Exception as e:
                    print("pageviews backfill " + project + " " + str(chunk_start) + " skipped, " + str(e))
                time.sleep(2.0)
                chunk_start = chunk_end + datetime.timedelta(days=1)
    return added
