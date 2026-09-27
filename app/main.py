import os
import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from app import config
from app import scheduler
from app.models import registry
from app.api import overview, risk, demand, monitoring, insights, rm, market, guests

started_at = datetime.datetime.now(datetime.timezone.utc)


# start the scheduler with the app and shut it down cleanly
@asynccontextmanager
async def lifespan(app):
    scheduler.bootstrap()
    scheduler.start()
    yield
    scheduler.scheduler.shutdown(wait=False)


app = FastAPI(title=config.app_name, lifespan=lifespan)
app.include_router(overview.router)
app.include_router(risk.router)
app.include_router(demand.router)
app.include_router(monitoring.router)
app.include_router(insights.router)
app.include_router(rm.router)
app.include_router(market.router)
app.include_router(guests.router)


# liveness endpoint used by the docker healthcheck and the reverse proxy
@app.get("/api/health")
def health():
    uptime = (datetime.datetime.now(datetime.timezone.utc) - started_at).total_seconds()
    return {"status": "ok", "app": config.app_name, "uptime_seconds": round(uptime), "champion": registry.champion_name(), "jobs": scheduler.status()}


# grafana links of the rail: redirect to the grafana url when no reverse proxy serves /grafana
@app.get("/grafana/{path:path}")
def grafana(path: str):
    return RedirectResponse(config.grafana_url.rstrip("/") + "/" + path)


# dashboard entry page
@app.get("/")
def index():
    return FileResponse(os.path.join(config.UI_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=config.UI_DIR), name="static")
