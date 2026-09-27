import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from app import db
from app.feeds import common, open_meteo, frankfurter, holidays, wikimedia, eurostat, travelbi
from app.models import score
from app.models import monitoring
from app.models import retrain
from app import dbt_runner

scheduler = BackgroundScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600})


# hourly weather forecast and fx refresh, the fx history backfill returns at once when the years are complete
def job_feeds_hourly():
    common.run_feed("open_meteo_forecast", open_meteo.fetch_forecast)
    common.run_feed("frankfurter_latest", frankfurter.fetch_latest)
    common.run_feed("frankfurter_history", frankfurter.backfill_history)


# daily pageviews, eurostat, official rates, holidays and weather archive refresh; the history backfills return at once when their years are complete
def job_feeds_daily():
    common.run_feed("wikimedia_refresh", wikimedia.refresh)
    common.run_feed("wikimedia_gaps", wikimedia.backfill)
    common.run_feed("eurostat", eurostat.refresh)
    common.run_feed("eurostat_regions", eurostat.refresh_regions)
    common.run_feed("eurostat_prices", eurostat.refresh_prices)
    common.run_feed("travelbi_adr", travelbi.refresh)
    common.run_feed("holidays_refresh", holidays.refresh)
    common.run_feed("holidays_history", holidays.backfill_history)
    common.run_feed("open_meteo_archive", open_meteo.refresh_archive)
    common.run_feed("open_meteo_history", open_meteo.backfill_history)


# score bookings that arrived on the replay stream
def job_replay_tick():
    from app import cache
    n = score.score_new_bookings()
    if n:
        cache.clear()
    print("replay tick scored " + str(n) + " new bookings")


# daily rebuild of marts, portfolio rescoring and monitoring
def job_daily_models():
    from app import cache
    dbt_runner.build()
    n = score.score_on_books()
    cache.clear()
    print("on books rescored " + str(n))
    monitoring.run_drift()
    monitoring.run_performance()
    from app.analytics import recommendations
    recommendations.run()


# recompute today's recommendations from the live numbers
def job_recommendations():
    from app.analytics import recommendations
    recommendations.run()


# weekly champion challenger retrain
def job_weekly_retrain():
    retrain.run()


# register all jobs and start the scheduler
def start():
    scheduler.add_job(job_feeds_hourly, "interval", hours=1, id="feeds_hourly")
    scheduler.add_job(job_feeds_daily, "cron", hour=2, minute=20, id="feeds_daily")
    scheduler.add_job(job_replay_tick, "interval", minutes=10, id="replay_tick")
    scheduler.add_job(job_daily_models, "cron", hour=3, minute=10, id="daily_models")
    scheduler.add_job(job_weekly_retrain, "cron", day_of_week="sun", hour=4, minute=0, id="weekly_retrain")
    scheduler.add_job(job_export, "cron", hour=3, minute=40, id="export")
    scheduler.add_job(job_refresh_notebooks, "cron", day_of_week="mon", hour=5, minute=0, id="refresh_notebooks")
    scheduler.add_job(job_airbnb_refresh, "cron", day=3, hour=6, minute=0, id="airbnb_refresh")
    scheduler.add_job(job_synthetic_daily, "cron", hour=0, minute=20, id="synthetic_daily")
    scheduler.start()


# work that must exist before the first request, run once at startup
def bootstrap():
    score.ensure_tables()
    monitoring.ensure_tables()
    have = db.query_df("select count(*) as n from scores.on_books where as_of = current_date")["n"][0]
    if have == 0:
        scheduler.add_job(job_daily_models, id="bootstrap_models")
    scheduler.add_job(job_replay_tick, id="bootstrap_tick")
    scheduler.add_job(job_feeds_hourly, id="bootstrap_feeds")
    from app.synthetic import run
    if run.table_exists("synthetic", "bookings") and run.batch_overdue():
        scheduler.add_job(job_synthetic_daily, id="bootstrap_synthetic")


# list of jobs with next run times for the health endpoint
def status():
    out = []
    for job in scheduler.get_jobs():
        out.append({"id": job.id, "next_run": str(job.next_run_time)})
    return out


# recompute drift and realised performance from scratch
def job_monitoring():
    db.execute("delete from monitoring.performance")
    monitoring.run_drift()
    n = monitoring.run_performance()
    print("performance rows evaluated " + str(n))


# wipe live scores and monitoring so they are rebuilt with the current champion
def job_reset_scores():
    db.execute("delete from scores.booking_time")
    db.execute("delete from scores.on_books")
    db.execute("delete from scores.score_runs")
    db.execute("delete from monitoring.performance")
    from app import cache
    cache.clear()
    print("scores reset")
    job_replay_tick()
    job_daily_models()


# export staged bookings and feed tables to parquet for the notebooks
def job_export():
    from app.models import export
    export.export_bookings()
    export.export_feeds()
    print("parquet export done")


# load the inside airbnb lisbon snapshot files from data/raw
def job_load_airbnb():
    from app.feeds import airbnb
    airbnb.load()


# monthly check for a newer inside airbnb snapshot, the current data stays when anything fails
def job_airbnb_refresh(force=False):
    from app.feeds import airbnb
    rows = common.run_feed("airbnb_snapshot", lambda: airbnb.refresh(force))
    if rows:
        job_refresh_notebooks()


# same check but forcing a download of the latest snapshot
def job_airbnb_refresh_force():
    job_airbnb_refresh(True)


# daily batch of the synthetic stream: emit the bookings made since the last batch from today's signals, catch up after a downtime, then rebuild the marts
def job_synthetic_daily():
    from app.synthetic import run
    from app import cache
    emitted = run.daily_batch()
    if emitted > 0:
        dbt_runner.build()
        cache.clear()
    print("synthetic daily batch done, bookings emitted " + str(emitted))


# full regeneration of the synthetic history, manual only
def job_synthetic_full():
    from app.synthetic import run
    from app import cache
    run.full_run()
    dbt_runner.build()
    cache.clear()
    print("synthetic stream regenerated")


# re-run the analysis notebooks so their exported forecasts and tables follow the data
def job_refresh_notebooks():
    import subprocess
    import os
    job_export()
    notebooks_dir = os.path.join(os.getcwd(), "notebooks")
    for name in ["04_demand_forecast.ipynb", "03_price_elasticity.ipynb", "07_review_aspects.ipynb", "05_causal_channel.ipynb"]:
        result = subprocess.run(["jupyter", "nbconvert", "--to", "notebook", "--execute", "--inplace", "--ExecutePreprocessor.timeout=2400", name], cwd=notebooks_dir, capture_output=True, text=True)
        status = "ok" if result.returncode == 0 else "error"
        common.log_run("notebook_" + name.split("_")[0], status, 0, result.stderr[-300:] if status == "error" else "")
        print("notebook " + name + " " + status)
