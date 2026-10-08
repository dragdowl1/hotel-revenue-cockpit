# Lisbon Lodging Cockpit

Revenue management cockpit for a two-hotel portfolio: a synthetic booking stream on today's calendar (built from 119,390 real 2015–2017 bookings and live public signals), nightly cancellation scoring, an eight-week demand forecast, causal channel and price analyses, a rules engine with euro-valued recommendations, and model monitoring with champion–challenger retraining. Single-server, one DuckDB file, everything in Docker.

**Live demo:** https://p1.mldlprojetcs.duckdns.org (Grafana at `/grafana`)

```
                         ┌──────────────────────────── docker compose ────────────────────────────┐
 public feeds ──HTTP──▶  │  app (FastAPI + APScheduler, 1 process, 1 DuckDB writer)               │
 open-meteo, frankfurter │   ├─ feeds/        hourly + daily pulls → raw.*                         │
 nager/openholidays      │   ├─ synthetic/    daily batch of the booking stream → synthetic.*      │
 wikimedia, eurostat     │   ├─ dbt_runner    dbt build → staging.*, marts.*  (schema tests)        │
 turismo de portugal     │   ├─ models/       score new + on-books bookings → scores.*             │
 inside airbnb           │   │               weekly retrain, PSI drift, realised quality           │
                         │   ├─ analytics/    rm metrics, 13 recommendation rules → recs.*         │
                         │   └─ api/          JSON + static ECharts UI          :8001 (localhost)  │
                         │  grafana (Infinity datasource → app API)             :3000 (localhost)  │
                         │  notebooks/  (jupyter nbconvert, weekly)  → data/models/*.json|joblib   │
                         └────────────────────────────────────────────────────────────────────────┘
```

## Stack

| Layer | Choice | Notes |
|---|---|---|
| Storage | DuckDB 1.x, Parquet | one file, columnar; all writers in the app process behind a lock |
| Transform | dbt-duckdb | `staging` (as-of joins, outcome masking) → `marts`; schema tests |
| Serving | FastAPI, uvicorn (1 worker), APScheduler 3 | 10-minute JSON cache cleared by scoring and nightly jobs |
| ML | LightGBM, scikit-learn, statsmodels, SHAP | landmark sampling, isotonic calibration, AIPW, fixed-effects OLS |
| UI | ECharts, vanilla JS | static, no build step |
| Monitoring | Grafana OSS 12 + Infinity | dashboards and alert rule provisioned from `grafana/` |
| Runtime | Docker Compose, Python 3.12 | `requirements.lock` pins 177 packages |

## Data

* [Hotel Booking Demand](https://doi.org/10.1016/j.dib.2018.11.126) (Antonio, de Almeida, Nunes 2019, CC BY 4.0) — the atoms of the stream, fetched by `scripts/fetch_raw.py`.
* Feeds (free, keyless): Open-Meteo (forecast, ERA5), Frankfurter (ECB), Nager.Date + OpenHolidays, Wikimedia pageviews, Eurostat `tour_occ_nim` / `prc_hicp_midx`, Turismo de Portugal RevPAR/ADR (xlsx), Inside Airbnb Lisbon (CC BY 4.0).
* Stream (`app/synthetic/`): real bookings resampled within hotel × month and augmented; outcomes redrawn from a cross-fitted world model (OOF AUC 0.947); volume from Eurostat nights, market shares from attention and FX, rates from official regional ADR, channel mix ramped to industry shares, stop-sell at capacity + 2 %, events (`dbt/seeds/events.csv`) flip outcomes through a deposit-weighted hazard. Outcomes are revealed only when a booking leaves the books (`outcome_known`), so nothing trains on the future.

## Models

| Model | Method | Validation | Artifact |
|---|---|---|---|
| Cancellation risk | LightGBM on landmark rows, exposure + dynamic class weights, isotonic calibration (exposure-weighted), cost threshold | time-based snapshot −90 d, 120 d validation cohort, tests on-books / next 8 weeks / post-snapshot; leakage audit vs. source atoms | `data/models/cancellation_v*.joblib` + `_metrics.json` + `_curves.json` |
| Demand forecast | weekly arrivals (arrived and not cancelled, open bookings past their arrival date included); per horizon LightGBM (lag 52/104, level, rooms on books, pickup ratio, calendar) vs. seasonal naive, ETS, Fourier-ARIMA, pickup | rolling backtest, 52 origins × 8 weeks, MAPE | `forecast.json` |
| Channel effect | AIPW, 5-fold cross-fitted boosted nuisances, overlap trim, E-value, placebo | influence-score SE, balance SMD | `causal_channel.json` |
| Price elasticity | within-transformed FE OLS (hotel × week × segment), cluster bootstrap, placebo; hedonic LightGBM on Airbnb | cluster-robust SE, CV RMSE/R² | `elasticity.json` |
| Review aspects | keyword lexicons, negative share per aspect | — | `review_aspects.json` |

Selection rule for the boosted models: among configurations with train–validation log-loss gap ≤ 0.03, lowest validation log loss. Weekly challenger promoted only if it beats the champion on the last 60 days of resolved arrivals. Drift: PSI, equal-width bins, champion features only.

## Run locally

Requirements: Docker ≥ 24 with Compose v2, ~6 GB free disk, 4 GB RAM for the app container.

```bash
cp .env.example .env          # set GF_SECURITY_ADMIN_PASSWORD and ADMIN_TOKEN
sed -i "s/^LOCAL_UID=.*/LOCAL_UID=$(id -u)/; s/^LOCAL_GID=.*/LOCAL_GID=$(id -g)/" .env   # linux/mac: files written by the container stay yours
docker compose up -d --build  # app on http://localhost:8001, grafana on http://localhost:3000
./scripts/bootstrap.sh        # first run only, see below
```

`bootstrap.sh` stops the API (DuckDB allows one writing process) and runs each step in a one-off container: raw CSV download → `raw.*` → feeds → synthetic stream (2014 → today + 400 d) → `dbt build` → Inside Airbnb snapshot (optional, ~300 MB) → Parquet export → notebooks 02, 04, 03, 05, 07 → API back up. Measured on 2 cores: feeds ~10 min, stream generation ~10 min, dbt 35 s, snapshot and export < 1 min, notebooks 47 min (cancellation 12, forecast 14, channel effect 18, elasticity 3); about 70 min in total, less with more cores. Feeds with long backfills (Wikipedia pageviews) complete over the following daily runs.

Manual equivalents (stop the API first, or run them while it is down; a second process cannot open the DuckDB file while a job holds it):

```bash
docker compose stop app
docker compose run --rm -w /srv -e PYTHONPATH=/srv app python -m app.synthetic.run        # full regeneration
docker compose run --rm -w /srv/dbt -e DBT_PROFILES_DIR=/srv/dbt -e DBT_DUCKDB_PATH=/srv/data/lodging.duckdb app dbt build
docker compose run --rm -w /srv/notebooks app jupyter nbconvert --to notebook --execute --inplace 02_cancellation_model.ipynb
docker compose up -d app
curl -X POST -H "X-Admin-Token: $ADMIN_TOKEN" http://localhost:8001/api/monitoring/run/daily_models   # any job id, inside the running API
```

Job ids: `feeds_hourly feeds_daily replay_tick daily_models weekly_retrain monitoring reset_scores export recommendations refresh_notebooks airbnb_refresh airbnb_refresh_force synthetic_daily synthetic_full`.

Switch to the plain replay of the real 2015–2017 data: `bookings_source: raw` and `replay_shift_days: 3654` in `dbt/dbt_project.yml`, same value in `app/replay/clock.py`, then `dbt build`.

## Layout

```
app/
  api/          routers: overview, risk, demand, rm, insights, guests, market, monitoring
  analytics/    rm.py (pace, pickup, booking-curve forecast), recommendations.py (13 rules), hazard.py
  feeds/        one module per source, common.run_feed logs every run
  models/       features.py, preprocess.py, retrain.py, score.py, monitoring.py, registry.py, export.py
  synthetic/    pool.py (atoms), outcomes.py (world model), signals.py, generator.py, run.py
  replay/       clock.py (as-of views of the books)
  scheduler.py  APScheduler jobs; main.py FastAPI app
dbt/            models/staging, models/marts, seeds/events.csv, schema tests
grafana/        provisioning (datasource, dashboards, alert rule), dashboards/*.json
notebooks/      01 EDA · 02 cancellation · 03 elasticity · 04 forecast · 05 channel effect · 06 review embeddings · 07 review aspects · 08–11 experiment protocols (real + synth)
scripts/        fetch_raw.py, load_raw.py, bootstrap.sh
ui/             index.html, app.js, app.css, echarts theme
data/           duckdb file, raw, parquet, models (git-ignored)
```

## API

`/api/health` · `/api/overview/*` · `/api/risk/*` · `/api/demand/*` · `/api/rm/*` (recommendations, forecast_backtest) · `/api/insights/*` · `/api/guests/*` · `/api/market/*` · `/api/monitoring/*` (catalog, catalog_metrics, curves, importance, calibration, drift, performance, alerts, experiments, events, synthetic, run/{job}). OpenAPI at `/docs`.

## Security

Ports bind to `127.0.0.1` only; put a reverse proxy with TLS in front for remote access. Containers drop all capabilities, run with `no-new-privileges`, mount code read-only and run as an unprivileged uid (your host uid locally). Query parameters are bounded; the admin token is compared in constant time. `POST /api/monitoring/run/*` requires `X-Admin-Token`. Grafana anonymous role is Viewer. No personal data is stored: bookings carry attributes and aggregates only, guest pages need ≥ 50 resolved bookings per rate.

## License

Code: MIT (see `LICENSE`). Data: Hotel Booking Demand and Inside Airbnb are CC BY 4.0; Eurostat, Turismo de Portugal and the other feeds under their own terms.
