# Hotel Revenue Cockpit

Revenue management cockpit for a two-hotel portfolio: a synthetic booking stream on today's calendar (built from 119,390 real 2015–2017 bookings and live public signals), nightly cancellation scoring, an eight-week demand forecast, causal channel and price analyses, a rules engine with euro-valued recommendations, and model monitoring with champion–challenger retraining. Single-server, one DuckDB file, everything in Docker.

**See Live demo [here](https://p1.mldlprojetcs.duckdns.org)** (Grafana at `/grafana`)

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

`/api/health` · `/api/overview/*` · `/api/risk/*` · `/api/demand/*` · `/api/rm/*` (recommendations, forecast_backtest) · `/api/insights/*` · `/api/guests/*` · `/api/market/*` · `/api/monitoring/*` (catalog, catalog_metrics, curves, importance, calibration, drift, performance, alerts, experiments, events, synthetic, run/{job}). 

## Security

Ports bind to `127.0.0.1` only; put a reverse proxy with TLS in front for remote access. Containers drop all capabilities, run with `no-new-privileges`, mount code read-only and run as an unprivileged uid (your host uid locally). Query parameters are bounded; the admin token is compared in constant time. `POST /api/monitoring/run/*` requires `X-Admin-Token`. Grafana anonymous role is Viewer. No personal data is stored: bookings carry attributes and aggregates only, guest pages need ≥ 50 resolved bookings per rate.

## License

Code: MIT (see `LICENSE`). Data: Hotel Booking Demand and Inside Airbnb are CC BY 4.0; Eurostat, Turismo de Portugal and the other feeds under their own terms.

## Sources

**Data**

* Hotel Booking Demand dataset, Antonio, de Almeida and Nunes, 2019, Data in Brief: https://doi.org/10.1016/j.dib.2018.11.126
* TidyTuesday mirror of the dataset (2020-02-11): https://github.com/rfordatascience/tidytuesday/tree/master/data/2020/2020-02-11
* Eurostat, nights spent at tourist accommodation (tour_occ_nim): https://ec.europa.eu/eurostat/databrowser/view/tour_occ_nim/default/table
* Eurostat, HICP monthly index (prc_hicp_midx): https://ec.europa.eu/eurostat/databrowser/view/prc_hicp_midx/default/table
* Turismo de Portugal, RevPAR and ADR by region and star class: https://travelbi.turismodeportugal.pt/en/accommodation/revpar-and-adr/
* Turismo de Portugal, occupancy rate by room and bed: https://travelbi.turismodeportugal.pt/en/accommodation/ocupancy-rate-roombed/
* Open-Meteo weather API (forecast and ERA5 archive): https://open-meteo.com/
* Frankfurter, ECB exchange rates: https://www.frankfurter.app/
* Nager.Date public holidays: https://date.nager.at/
* OpenHolidays API, school and public holidays: https://www.openholidaysapi.org/
* Wikimedia pageviews API: https://wikimedia.org/api/rest_v1/
* Inside Airbnb, Lisbon data: https://insideairbnb.com/get-the-data/

**Industry benchmarks used to calibrate the stream**

* HOTREC European Hotel Distribution Study 2024: https://www.hotrec.eu/media/static/files/import/all_news_2024_2024_21/hotrec-distribution-study-2024.pdf
* D-EDGE Hotel Distribution Report 2024: https://www.d-edge.com/hotel-distribution-report-2024-have-direct-bookings-reached-a-peak/
* D-EDGE, avoiding guests ghosting (no shows and prepaid cancellations): https://www.d-edge.com/avoid-guests-ghosting/
* D-EDGE cancellation study via Hotel Management: https://www.hotelmanagement.net/tech/study-cancelation-rate-at-40-as-otas-push-free-change-policy
* Hospitality Net, preventing no shows and last minute cancellations: https://www.hospitalitynet.org/news/4124422/how-to-prevent-hotel-no-show-and-last-minute-cancellations
* SiteMinder Hotel Booking Trends 2025: https://www.siteminder.com/news/siteminder-hotel-booking-trends-2025/
* Mews, hotel booking trends: https://www.mews.com/en/blog/hotel-booking-trends
* HVS Lisbon Market Pulse 2025: https://www.hvs.com/article/10177-lisbon-market-pulse-2025-award-winning-city-destination
* AHP summer 2024 occupancy via TNews: https://tnews.pt/taxa-de-ocupacao-sobe-para-81-no-verao-de-2024-com-destaque-para-algarve-acores-e-madeira/
* AHETA Algarve December 2024 occupancy via Vida Imobiliaria: https://vidaimobiliaria.com/noticias/hoteis/ocupa%C3%A7%C3%A3o-por-quarto-no-algarve-cresceu-para-349-em-dezembro/
* IMF WP/22/24, exchange rates and tourism: https://www.imf.org/-/media/Files/Publications/WP/2022/English/wpiea2022024-print-pdf.ashx
* Peng, Song, Crouch and Witt, 2015, tourism demand elasticities: https://journals.sagepub.com/doi/10.1177/0047287514528283

**Evidence behind the event rules of the stream**

* Empirica, 2023, short term rental cancellations in March 2020: https://pmc.ncbi.nlm.nih.gov/articles/PMC10134705/
* Net Affinity, cancellation trends: https://blog.netaffinity.com/cancellation-trends-where-do-they-stand-and-how-can-you-overcome-them
* Skift, 28 April 2025, Iberian blackout: https://skift.com/2025/04/28/blackouts-across-spain-and-portugal-hit-travel/
* Portugal Resident, airport strike: https://www.portugalresident.com/portugal-airport-strike-causes-major-flight-disruptions-as-hundreds-of-services-cancelled/
* WEF, how destinations bounce back after attacks: https://www.weforum.org/stories/2016/03/how-destinations-can-bounce-back-after-terrorist-attacks/
* Anguera-Torrell and Boto-Garcia, 2025: https://doi.org/10.1177/00472875241266612
* Rossello, Becken and Santana-Gallego, 2020: https://pmc.ncbi.nlm.nih.gov/articles/PMC7115519/
* OECD Tourism Trends and Policies 2022, Portugal: https://www.oecd.org/en/publications/oecd-tourism-trends-and-policies-2022_a8dd3019-en/full-report/portugal_26342d91.html
* INE, 2023, tourism statistics release: https://www.ine.pt/xportal/xmain?xpid=INE&xpgid=ine_destaques&DESTAQUESdest_boui=646074543&DESTAQUEStema=55581&DESTAQUESmodo=2
* Borneo Post / AFP, 2016, Portugal tourism after attacks elsewhere: https://www.theborneopost.com/2016/07/01/portugal-tourism-booms-as-terror-strikes-around-mediterranean/
* Krajnak, 2021, terrorism and tourism substitution: https://dx.doi.org/10.1177/1354816620938900
* Oeconomus, 2023, tourism during the war: https://www.oeconomus.hu/en/analyses/tourism-during-the-war-how-russian-ukrainian-and-european-tourism-changed/
* Otrachshenko and Nunes, 2019, wildfires and overnight stays: https://ssrn.com/abstract=3438168
* JRC, 2023, climate and tourism demand: https://publications.jrc.ec.europa.eu/repository/handle/JRC131508
* Euronews, July 2023, heatwave and travel: https://euronews.com/travel/2023/07/19/should-you-cancel-your-trip-due-to-the-heatwave-heres-how-extreme-heat-is-impacting-travel
* Airbnb lead times 2018 to 2022 (arXiv): https://arxiv.org/html/2501.10535v1

