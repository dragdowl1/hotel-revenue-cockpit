import os
import datetime
from dbt.cli.main import dbtRunner
from app import config
from app import db


# run dbt build in process and store the summary in the feed_runs table
def build():
    os.environ["DBT_PROFILES_DIR"] = config.DBT_DIR
    os.environ["DBT_DUCKDB_PATH"] = config.DB_PATH
    cwd = os.getcwd()
    os.chdir(config.DBT_DIR)
    try:
        with db.db_lock:
            result = dbtRunner().invoke(["build", "--no-use-colors", "--quiet"])
    finally:
        os.chdir(cwd)
    ok = 0
    failed = 0
    if result.result is not None:
        for r in result.result:
            if str(r.status) in ("success", "pass"):
                ok = ok + 1
            else:
                failed = failed + 1
    status = "ok" if result.success else "error"
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    db.execute("insert into raw.feed_runs values ('dbt_build', ?, ?, ?, ?)", [now, status, ok, "failed nodes " + str(failed)])
    print("dbt build " + status + ", ok " + str(ok) + ", failed " + str(failed))
    return result.success
