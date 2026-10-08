from app.cache import cached
from app.analytics import rm
from app.replay import clock
from typing import Annotated
from app.api.common import records
from fastapi import APIRouter, Query
from app.analytics import recommendations

router = APIRouter(prefix="/api/rm")


# revenue manager's headline numbers for the next 30 days
@router.get("/brief")
@cached
def brief(days: Annotated[int, Query(ge=1, le=3660)] = 30):
    return {"as_of": str(clock.today()), "days": days, "hotels": rm.brief(clock.today(), days)}


# forecast per stay date from the booking curve and the cancellation model
@router.get("/forecast")
@cached
def forecast(days: Annotated[int, Query(ge=1, le=3660)] = 90):
    return records(rm.forecast_by_date(clock.today(), days))


# pace curve, rooms on the books by days before arrival, this year against last year
@router.get("/pace")
@cached
def pace(days: Annotated[int, Query(ge=1, le=3660)] = 30):
    return rm.pace_curve(clock.today(), days)


# daily gross pickup and cancellations for the last weeks
@router.get("/pickup")
@cached
def pickup(weeks: Annotated[int, Query(ge=1, le=520)] = 6):
    return records(rm.pickup_daily(clock.today(), weeks))


# segment mix of the books, this year against last year
@router.get("/mix")
@cached
def mix(days: Annotated[int, Query(ge=1, le=3660)] = 90):
    return rm.channel_mix(clock.today(), days)


# backtest of the booking curve forecast at weekly origins over the last weeks, forecast against realised rooms per stay date
@router.get("/forecast_backtest")
@cached
def forecast_backtest(origins: Annotated[int, Query(ge=1, le=104)] = 12):
    return rm.forecast_backtest(clock.today(), origins)


# stored recommendations with changes since the previous run
@router.get("/recommendations")
def recs():
    return recommendations.latest()
