import datetime
import time
import httpx
from app import config
from app import db


# shared http client with the project user agent and a short timeout
def client():
    headers = {"User-Agent": config.user_agent, "Accept": "application/json"}
    return httpx.Client(headers=headers, timeout=20.0, follow_redirects=True)


# get json with retry and backoff for rate limits and transient errors, honouring a retry after header
def get_json(url, params=None, retries=5):
    wait = 2.0
    with client() as c:
        for attempt in range(retries):
            r = c.get(url, params=params)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                retry_after = r.headers.get("Retry-After")
                time.sleep(float(retry_after) if retry_after and retry_after.isdigit() else wait)
                wait = min(wait * 2, 60.0)
                continue
            r.raise_for_status()
    raise RuntimeError("request failed after retries: " + url)


# get a text page with a browser user agent, for report pages that refuse api clients
def get_text(url, retries=3):
    wait = 2.0
    with httpx.Client(headers={"User-Agent": "Mozilla/5.0 " + config.user_agent}, timeout=30.0, follow_redirects=True) as c:
        for attempt in range(retries):
            r = c.get(url)
            if r.status_code == 200:
                return r.text
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(wait)
                wait = wait * 2
                continue
            r.raise_for_status()
    raise RuntimeError("request failed after retries: " + url)


# get a binary file such as a zip archive
def get_bytes(url, retries=3):
    wait = 2.0
    with httpx.Client(headers={"User-Agent": "Mozilla/5.0 " + config.user_agent}, timeout=60.0, follow_redirects=True) as c:
        for attempt in range(retries):
            r = c.get(url)
            if r.status_code == 200:
                return r.content
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(wait)
                wait = wait * 2
                continue
            r.raise_for_status()
    raise RuntimeError("request failed after retries: " + url)


# record one feed run in the feed_runs table
def log_run(feed, status, rows_added, message=""):
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    db.execute("insert into raw.feed_runs values (?, ?, ?, ?, ?)", [feed, now, status, rows_added, message[:500]])


# run a feed function and log its outcome without raising
def run_feed(feed, fn):
    try:
        rows = fn()
        log_run(feed, "ok", rows)
        print("feed " + feed + " ok, rows added " + str(rows))
        return rows
    except Exception as e:
        log_run(feed, "error", 0, str(e))
        print("feed " + feed + " error, " + str(e))
        return 0


# current utc timestamp without timezone for duckdb
def now_utc():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
