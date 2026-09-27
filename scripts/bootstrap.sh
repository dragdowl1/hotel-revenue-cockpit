#!/usr/bin/env bash
# first run: raw data, feeds, synthetic stream, dbt marts, parquet export, model notebooks.
# duckdb allows one writing process, so the api is stopped and every step runs in a one-off container.
set -euo pipefail
cd "$(dirname "$0")/.."
run() { docker compose run --rm -T -w /srv -e PYTHONPATH=/srv app "$@"; }

docker compose stop app

echo "1/7 raw bookings"
run python scripts/fetch_raw.py
run python scripts/load_raw.py

echo "2/7 public feeds (weather, fx, holidays, wikipedia attention, eurostat, official adr); long backfills continue in the daily job"
run python -c "from app import scheduler; scheduler.job_feeds_hourly(); scheduler.job_feeds_daily()"

echo "3/7 synthetic booking stream (2014 to today plus 400 days)"
run python -m app.synthetic.run

echo "4/7 dbt marts"
docker compose run --rm -T -w /srv/dbt -e DBT_PROFILES_DIR=/srv/dbt -e DBT_DUCKDB_PATH=/srv/data/lodging.duckdb app dbt build

echo "5/7 inside airbnb lisbon snapshot (about 300 MB download, market page and notebooks 03 and 07)"
run python -c "from app.feeds import airbnb; airbnb.refresh(True)" || echo "airbnb snapshot skipped, market page stays empty"

echo "6/7 parquet export for the notebooks"
run python -m app.models.export

echo "7/7 model notebooks (cancellation, forecast, elasticity, channel effect, review aspects)"
for nb in 02_cancellation_model 04_demand_forecast 03_price_elasticity 05_causal_channel 07_review_aspects; do
  docker compose run --rm -T -w /srv/notebooks app jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=7200 "$nb.ipynb"
done

docker compose up -d app
echo "done: dashboard http://localhost:8001, grafana http://localhost:3000"
