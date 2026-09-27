import numpy as np
from app import db

grid = [120, 90, 60, 45, 30, 21, 14, 7, 3, 1, 0]
segments = ["Online TA", "Offline TA/TO", "Direct", "Groups", "Corporate"]


# share of the cancellations that will ever happen already known at each number of days before arrival, per hotel and segment
def known_share():
    frame = db.query_df(
        "select hotel, market_segment, lead_time, is_canceled, datediff('day', reservation_status_date, arrival_date) as days_before_arrival "
        "from staging.stg_bookings where market_segment in ('Online TA', 'Offline TA/TO', 'Direct', 'Groups', 'Corporate') and lead_time >= 30")
    out = []
    for (hotel, seg), part in frame.groupby(["hotel", "market_segment"]):
        canceled = part["is_canceled"].values == 1
        days_left = part["days_before_arrival"].clip(lower=0).values
        total = canceled.sum()
        row = {"hotel": hotel, "market_segment": seg, "bookings": int(len(part)), "cancel_rate": round(float(canceled.mean()), 4)}
        for g in grid:
            row["t" + str(g)] = round(float((canceled & (days_left >= g)).sum() / total), 4) if total >= 50 else None
        out.append(row)
    return out


# share of group cancellations known at a given number of days out, both hotels pooled
def group_share_known(days=30):
    rows = [r for r in known_share() if r["market_segment"] == "Groups" and r.get("t" + str(days)) is not None]
    if not rows:
        return None
    weights = np.array([r["bookings"] * r["cancel_rate"] for r in rows])
    values = np.array([r["t" + str(days)] for r in rows])
    return float(np.sum(weights * values) / np.sum(weights))
