import os
import json
import datetime
import numpy as np
import pandas as pd
from scipy.stats import norm
from app import config
from app import db
from app.analytics import rm
from app.replay import clock

# thresholds that turn live numbers into actions
sell_out_occupancy = 0.95
soft_occupancy = 0.65
pace_ahead = 1.10
pace_behind = 0.90
walk_risk_max = 0.10
min_group_rooms = 10
high_risk_p = 0.5
high_value = 300.0
agent_min_bookings = 30
agent_excess_rate = 0.15
one_night_share_max = 0.30


# integer euros with thousands separators for the card texts
def eur(value):
    digits = str(int(round(abs(float(value)))))
    groups = []
    while len(digits) > 3:
        groups.insert(0, digits[-3:])
        digits = digits[:-3]
    groups.insert(0, digits)
    return ("-" if value < 0 else "") + ",".join(groups) + " euros"


# percent with the sign attached
def pct(value, decimals=0):
    return str(round(float(value) * 100, decimals) if decimals else int(round(float(value) * 100))) + "%"


# json file exported by a notebook, empty dict when missing
def load_json(name):
    path = os.path.join(config.MODELS_DIR, name)
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


# create the recommendations table
def ensure_tables():
    db.execute("create schema if not exists recs")
    db.execute("create table if not exists recs.daily (run_date date, rec_id varchar, rule varchar, hotel varchar, priority integer, impact_eur double, title varchar, payload varchar)")


# accept rooms above capacity where the forecast sells out and expected cancellations cover the risk, one card per hotel
def rule_overbooking(fc, as_of):
    out = []
    near = fc[(fc["days_out"] <= 30) & (fc["forecast_occupancy"] >= sell_out_occupancy) & (fc["expected_cancels"] >= 3)]
    for hotel, part in near.groupby("hotel"):
        dates = []
        for r in part.to_dict("records"):
            sd = max(np.sqrt(r["expected_cancels"] * 0.6), 1.0)
            accept = int(np.floor(r["expected_cancels"] - norm.ppf(1 - walk_risk_max) * sd))
            if accept < 2:
                continue
            walk_risk = float(1 - norm.cdf((r["expected_cancels"] - accept) / sd))
            dates.append({"date": r["stay_date"], "rooms_otb": r["rooms_otb"], "capacity": r["capacity"], "expected_cancels": r["expected_cancels"], "accept": accept, "walk_risk": round(walk_risk, 3), "impact_eur": round(accept * r["adr_otb"])})
        if len(dates) == 0:
            continue
        total_rooms = sum(d["accept"] for d in dates)
        impact = sum(d["impact_eur"] for d in dates)
        out.append({"rule": "overbooking", "hotel": hotel, "date": dates[0]["date"] + " to " + dates[-1]["date"], "priority": 1 if min(part["days_out"]) <= 14 else 2, "impact_eur": impact,
                    "title": "Sell " + str(total_rooms) + " rooms above capacity across " + str(len(dates)) + " sell out dates at the " + hotel,
                    "detail": "Each date is forecast to sell out and expected cancellations exceed the extra rooms with a walk risk of at most " + pct(walk_risk_max) + "; first date " + dates[0]["date"] + ", largest allowance " + str(max(d["accept"] for d in dates)) + " rooms",
                    "action": "Keep selling past capacity by the allowance per date; allowances refresh nightly with the cancellation scores", "dates": dates})
    return out


# hold or raise rates on dates that sell out and are ahead of last year
def rule_rate(fc, elasticity, as_of):
    out = []
    ota = elasticity.get("pickup", {}).get("online_ta", {})
    e = ota.get("elasticity")
    p_value = ota.get("p")
    if e is None or e >= 0 or p_value is None or p_value > 0.05:
        e = None
    near = fc[(fc["days_out"] <= 45) & (fc["forecast_occupancy"] >= sell_out_occupancy)]
    for hotel, part in near.groupby("hotel"):
        if len(part) < 3:
            continue
        rooms = part["rooms_otb"].sum()
        adr = float(part["revenue_otb"].sum() / max(rooms, 1))
        uplift = 0.05
        lost_share = 0.0 if e is None else max(-e * uplift, 0)
        impact = rooms * adr * (uplift - lost_share * (1 + uplift)) if e is not None else rooms * adr * uplift
        out.append({"rule": "rate_hold", "hotel": hotel, "date": str(part["stay_date"].min()) + " to " + str(part["stay_date"].max()), "priority": 2, "impact_eur": round(float(impact)),
                    "title": "Close discounts and lift rates 5% on " + str(len(part)) + " sell out dates at the " + hotel,
                    "detail": "Forecast occupancy at or above " + pct(sell_out_occupancy) + " on " + str(len(part)) + " dates in the next 45 days, current rate on the books " + eur(adr) + "" + (", price elasticity is not identified from transaction data so the impact assumes no volume loss on sell out dates where demand already exceeds capacity" if e is None else ", the measured pickup elasticity of the Online TA channel is " + str(e) + " so a 5% lift loses about " + pct(lost_share, 1) + " of room nights, which the sell out dates absorb"),
                    "action": "Remove promotional rates and raise the best available rate by 5% for these dates"})
    return out


# stimulate demand on soft dates that are behind last year
def rule_stimulate(fc, brief, as_of):
    out = []
    soft = fc[(fc["days_out"] > 7) & (fc["days_out"] <= 60) & (fc["forecast_occupancy"] < soft_occupancy)]
    for hotel, part in soft.groupby("hotel"):
        if len(part) < 5 or brief[hotel]["pace_index"] >= pace_behind:
            continue
        gap = float(((soft_occupancy - part["forecast_occupancy"]) * part["capacity"]).sum())
        adr = float(part["revenue_otb"].sum() / max(part["rooms_otb"].sum(), 1))
        out.append({"rule": "stimulate", "hotel": hotel, "date": str(part["stay_date"].min()) + " to " + str(part["stay_date"].max()), "priority": 2, "impact_eur": round(gap * adr * 0.5),
                    "title": "Fill " + str(round(gap)) + " soft room nights at the " + hotel + " between " + str(part["stay_date"].min())[5:] + " and " + str(part["stay_date"].max())[5:],
                    "detail": str(len(part)) + " dates forecast below " + pct(soft_occupancy) + " occupancy, pace index " + str(brief[hotel]["pace_index"]) + " against last year; impact assumes half of the gap is recoverable at the current rate of " + eur(adr) + "",
                    "action": "Open a short lead promotion for Direct and Online TA on these dates and push the source markets with holidays in the window"})
    return out


# call or resell the high value bookings most likely to cancel in the next two weeks
def rule_confirm(as_of):
    frame = db.query_df(
        "select b.hotel, count(*) as bookings, round(sum(b.booking_value), 0) as value, round(sum(o.expected_loss), 0) as expected_loss "
        "from scores.on_books o join staging.stg_bookings b using (booking_id) where o.as_of = (select max(as_of) from scores.on_books) "
        "and b.replay_arrival_date > ? and b.replay_arrival_date <= ? and o.p_cancel >= ? and b.booking_value >= ? group by 1", [as_of, as_of + datetime.timedelta(days=14), high_risk_p, high_value])
    out = []
    for r in frame.to_dict("records"):
        if r["bookings"] < 3:
            continue
        out.append({"rule": "confirm", "hotel": r["hotel"], "date": "next 14 days", "priority": 1, "impact_eur": round(float(r["expected_loss"])),
                    "title": "Confirm " + str(int(r["bookings"])) + " high value arrivals at the " + r["hotel"] + " worth " + eur(r["value"]) + "",
                    "detail": "Each is more likely to cancel than not and worth at least " + eur(high_value) + "; expected loss " + eur(r["expected_loss"]) + "",
                    "action": "Reconfirm by email today, resell any room released, list is on the risk page"})
    return out


# review group blocks whose release deadline passes before most cancellations are known
def rule_groups(fc, as_of):
    from app.analytics import hazard as hz
    known = hz.group_share_known(30)
    known_text = "historically about " + pct(known) + " of group cancellations are known 30 days out" if known is not None else "most group cancellations are known well before arrival"
    frame = db.query_df(
        "select b.hotel, count(*) as bookings, sum(b.nights) as room_nights, round(sum(b.booking_value), 0) as value, round(avg(o.p_cancel), 3) as avg_p "
        "from scores.on_books o join staging.stg_bookings b using (booking_id) where o.as_of = (select max(as_of) from scores.on_books) "
        "and b.market_segment = 'Groups' and b.replay_arrival_date > ? and b.replay_arrival_date <= ? group by 1", [as_of + datetime.timedelta(days=30), as_of + datetime.timedelta(days=60)])
    out = []
    for r in frame.to_dict("records"):
        if r["room_nights"] < min_group_rooms:
            continue
        out.append({"rule": "groups", "hotel": r["hotel"], "date": "arrivals in 30 to 60 days", "priority": 2, "impact_eur": round(float(r["value"]) * float(r["avg_p"])),
                    "title": "Review " + str(int(r["room_nights"])) + " group room nights at the " + r["hotel"] + " before the 30 day release point",
                    "detail": str(int(r["bookings"])) + " group bookings worth " + eur(r["value"]) + " with an average cancellation probability of " + pct(r["avg_p"]) + "; " + known_text,
                    "action": "Ask for rooming lists or deposits now and release unconfirmed rooms at 30 days"})
    return out


# direct booking incentive when the Online TA share of the books grows and cancels more
def rule_channel(mix, causal, as_of):
    out = []
    cur = pd.DataFrame(mix["this_year"]); ly = pd.DataFrame(mix["last_year"])
    if len(cur) == 0 or len(ly) == 0:
        return out
    effect = causal.get("ipw_effect")
    by_lead = causal.get("by_lead", {})
    for hotel in rm.capacity:
        c = cur[cur["hotel"] == hotel]; l = ly[ly["hotel"] == hotel]
        share = float(c.loc[c["market_segment"] == "Online TA", "rooms"].sum() / max(c["rooms"].sum(), 1))
        share_ly = float(l.loc[l["market_segment"] == "Online TA", "rooms"].sum() / max(l["rooms"].sum(), 1))
        if share - share_ly < 0.05 or effect is None:
            continue
        lead_rooms = db.query_df("with x as (" + rm.stay_sql + ") select count(*) as rooms from x join staging.stg_bookings b using (booking_id) where x.hotel = ? and x.market_segment = 'Online TA' and b.lead_time >= 8 and x.booked <= ? and x.leaves > ? and x.stay_date > ? and x.stay_date <= ?", [hotel, as_of, as_of, as_of, as_of + datetime.timedelta(days=90)])
        ota_rooms = float(lead_rooms["rooms"][0])
        adr = float(c.loc[c["market_segment"] == "Online TA", "adr"].mean())
        shift = 0.10
        impact = ota_rooms * shift * adr * (-effect)
        out.append({"rule": "channel", "hotel": hotel, "date": "next 90 days", "priority": 3, "impact_eur": round(impact),
                    "title": "Online TA share of the books is up " + str(round((share - share_ly) * 100)) + " points at the " + hotel + ", fund a direct booking incentive",
                    "detail": "Online TA holds " + pct(share) + " of room nights against " + str(round(share_ly * 100)) + " last year; booking direct lowers cancellation by " + str(round(-effect * 100)) + " points for the same guest and trip (doubly robust estimate, none of it for bookings made within a week" + (", " + str(round(-by_lead.get("90+", 0) * 100)) + " points beyond 90 days" if "90+" in by_lead else "") + "), so shifting 10% of the Online TA rooms booked a week or more ahead protects about " + eur(impact) + " before commission savings",
                    "action": "Offer a direct only perk worth up to " + eur(adr * -effect) + " per booking on stays booked a week or more ahead, the expected loss avoided per room night"})
    return out


# agents whose recent bookings cancel far above the hotel norm
def rule_agents(as_of):
    frame = db.query_df(
        "select hotel, agent, count(*) as bookings, avg(is_canceled) as cancel_rate, round(sum(case when is_canceled = 1 then booking_value else 0 end), 0) as lost_value, avg(case when deposit_type = 'Non Refund' then 1 else 0 end) as non_refund_share "
        "from staging.stg_bookings where agent <> 'NULL' and replay_booking_date > ? and replay_booking_date <= ? and replay_leave_books_date <= ? group by 1, 2", [as_of - datetime.timedelta(days=180), as_of, as_of])
    out = []
    for hotel, part in frame.groupby("hotel"):
        prior = float(part["cancel_rate"].mul(part["bookings"]).sum() / part["bookings"].sum())
        part = part[part["bookings"] >= agent_min_bookings]
        part = part.assign(shrunk=(part["cancel_rate"] * part["bookings"] + prior * 50) / (part["bookings"] + 50))
        bad = part[part["shrunk"] >= prior + agent_excess_rate].sort_values("lost_value", ascending=False).head(3)
        for r in bad.to_dict("records"):
            if r["non_refund_share"] >= 0.5:
                out.append({"rule": "agent", "hotel": hotel, "date": "last 180 days", "priority": 3, "impact_eur": 0,
                            "title": "Agent " + str(r["agent"]) + " books non refundable and still cancels " + pct(r["shrunk"]) + " at the " + hotel + ", treat its bookings as holds",
                            "detail": str(int(r["bookings"])) + " resolved bookings in 180 days, " + pct(r["non_refund_share"]) + " non refundable; the revenue is kept but the rooms inflate the books and the forecast",
                            "action": "Exclude this agent's unconfirmed rooms from pace and forecast, and agree a cut off for rooming lists"})
            else:
                out.append({"rule": "agent", "hotel": hotel, "date": "last 180 days", "priority": 3, "impact_eur": round(float(r["lost_value"]) * 0.5),
                            "title": "Agent " + str(r["agent"]) + " cancels " + pct(r["shrunk"]) + " of its bookings at the " + hotel + ", ask for a deposit",
                            "detail": str(int(r["bookings"])) + " resolved bookings in 180 days, hotel norm " + pct(prior) + ", " + eur(r["lost_value"]) + " lost to cancellations; impact assumes a deposit halves the loss",
                            "action": "Move this agent to a deposit or non refundable rate plan"})
    return out


# source markets with a holiday in the window whose bookings are behind last year
def rule_markets(as_of):
    hol = db.query_df("select country, min(date) as start_date, max(date) as end_date, name, kind from raw.holidays where date > ? and date <= ? group by country, name, kind having count(*) >= 5 or kind = 'public' order by start_date", [as_of + datetime.timedelta(days=14), as_of + datetime.timedelta(days=75)])
    iso2 = {"PT": "PRT", "GB": "GBR", "FR": "FRA", "ES": "ESP", "DE": "DEU", "IT": "ITA", "IE": "IRL", "BE": "BEL", "BR": "BRA", "NL": "NLD", "US": "USA", "CH": "CHE"}
    out = []
    seen = set()
    for h in hol.to_dict("records"):
        country3 = iso2.get(h["country"])
        if country3 is None or h["kind"] != "school" or country3 in seen:
            continue
        cur = db.query_df("select count(*) as n, round(avg(adr), 0) as adr from staging.stg_bookings where country = ? and replay_booking_date <= ? and replay_leave_books_date > ? and replay_arrival_date between ? and ?", [country3, as_of, as_of, h["start_date"], h["end_date"]])
        ly = db.query_df("select count(*) as n from staging.stg_bookings where country = ? and replay_booking_date <= ? and replay_leave_books_date > ? and replay_arrival_date between ? and ?", [country3, as_of - datetime.timedelta(days=364), as_of - datetime.timedelta(days=364), pd.Timestamp(h["start_date"]) - pd.Timedelta(days=364), pd.Timestamp(h["end_date"]) - pd.Timedelta(days=364)])
        n, n_ly = int(cur["n"][0]), int(ly["n"][0])
        if n_ly < 20 or n >= n_ly * 0.8:
            continue
        seen.add(country3)
        adr = float(cur["adr"][0] or 100)
        out.append({"rule": "market", "hotel": "both", "date": str(h["start_date"])[:10] + " to " + str(h["end_date"])[:10], "priority": 2, "impact_eur": round((n_ly - n) * adr * 3),
                    "title": country3 + " bookings for its " + h["name"].lower() + " are " + pct((1 - n / n_ly)) + " behind last year",
                    "detail": str(n) + " bookings from " + country3 + " on the books for " + str(h["start_date"])[:10] + " to " + str(h["end_date"])[:10] + " against " + str(n_ly) + " at the same point last year; impact values the gap at three nights at " + eur(adr) + "",
                    "action": "Run a targeted campaign in " + country3 + " now, the holiday starts in " + str((pd.Timestamp(h["start_date"]).date() - as_of).days) + " days"})
    return out


# minimum stay on sell out dates crowded with one night bookings
def rule_min_stay(fc):
    out = []
    peak = fc[(fc["forecast_occupancy"] >= sell_out_occupancy) & (fc["one_night_share"] >= one_night_share_max) & (fc["days_out"] <= 60)]
    for hotel, part in peak.groupby("hotel"):
        if len(part) < 2:
            continue
        rooms = float((part["one_night_share"] * part["rooms_otb"]).sum())
        adr = float(part["revenue_otb"].sum() / max(part["rooms_otb"].sum(), 1))
        out.append({"rule": "min_stay", "hotel": hotel, "date": ", ".join(part["stay_date"].astype(str).str[5:].head(6)), "priority": 3, "impact_eur": round(rooms * adr * 0.5),
                    "title": "Apply a 2 night minimum on " + str(len(part)) + " sell out dates at the " + hotel,
                    "detail": "One night bookings hold " + str(round(rooms)) + " of the rooms on these dates; longer stays displace them and reduce turnover cost; impact assumes half the one night rooms convert to longer stays",
                    "action": "Set a 2 night minimum length of stay restriction for these arrival dates"})
    return out


# booking agents that stopped booking, a call before the account is lost
def rule_accounts(as_of):
    from app.api import guests
    acc = guests.accounts()
    at_risk = [a for a in acc["at_risk"] if a["segment"] == "at risk" and a["prior_revenue"] >= 20000]
    if len(at_risk) == 0:
        return []
    top = at_risk[:3]
    revenue = float(sum(a["prior_revenue"] for a in at_risk))
    names = ", ".join("agent " + str(a["agent"]) + " (" + eur(a["prior_revenue"]) + " the year before, silent for " + str(int(a["recency_days"])) + " days)" for a in top)
    return [{"rule": "accounts", "hotel": "both", "date": "last " + str(guests.dormant_days) + " days", "priority": 2, "impact_eur": round(revenue * 0.25),
             "title": str(len(at_risk)) + " booking agents worth " + eur(revenue) + " the year before have not booked for " + str(guests.dormant_days) + " days",
             "detail": names + "; each had at least " + str(guests.at_risk_min_bookings) + " bookings in the year before; impact assumes an account call recovers a quarter of that revenue",
             "action": "Call the accounts this week with an allotment or a contracted rate for the coming season, list on the agents and channels page"}]


# bookings on the books from guests with a prior cancellation and no deposit, ask for a deposit
def rule_history(as_of):
    from app.api import guests
    resolved = db.query_df(
        "select hotel, count(*) as n, avg(is_canceled) as rate from staging.stg_bookings where previous_cancellations > 0 and deposit_type = 'No Deposit' and replay_booking_date > ? and replay_booking_date <= ? and replay_leave_books_date <= ? group by 1",
        [as_of - datetime.timedelta(days=guests.window_days), as_of, as_of])
    books = db.query_df(
        "select hotel, count(*) as bookings, round(sum(booking_value), 0) as value from staging.stg_bookings where previous_cancellations > 0 and deposit_type = 'No Deposit' and replay_booking_date <= ? and replay_leave_books_date > ? and replay_arrival_date > ? and replay_arrival_date <= ? group by 1",
        [as_of, as_of, as_of, as_of + datetime.timedelta(days=60)])
    out = []
    for r in books.to_dict("records"):
        hist = resolved[resolved["hotel"] == r["hotel"]]
        if len(hist) == 0 or int(hist["n"].iloc[0]) < guests.min_rows or r["bookings"] < 5:
            continue
        rate = float(hist["rate"].iloc[0])
        out.append({"rule": "history", "hotel": r["hotel"], "date": "arrivals in 60 days", "priority": 2, "impact_eur": round(float(r["value"]) * rate * 0.5),
                    "title": str(int(r["bookings"])) + " bookings at the " + r["hotel"] + " come from guests who cancelled before and hold no deposit",
                    "detail": "Such bookings cancelled " + pct(rate) + " of the time in the last 12 months (" + str(int(hist["n"].iloc[0])) + " resolved cases); " + eur(r["value"]) + " on the books for the next 60 days; impact assumes a deposit halves the loss",
                    "action": "Ask for a deposit or move these bookings to a non refundable rate at reconfirmation"})
    return out


# bookings on the books with no engagement signal, a pre arrival contact before they cancel
def rule_engagement(as_of):
    from app.api import guests
    resolved = db.query_df(
        "select hotel, case when total_of_special_requests = 0 then 0 else 1 end as engaged, count(*) as n, avg(is_canceled) as rate from staging.stg_bookings where deposit_type = 'No Deposit' and lead_time >= 30 and replay_booking_date > ? and replay_booking_date <= ? and replay_leave_books_date <= ? group by 1, 2",
        [as_of - datetime.timedelta(days=guests.window_days), as_of, as_of])
    books = db.query_df(
        "select hotel, count(*) as bookings, round(sum(booking_value), 0) as value from staging.stg_bookings where total_of_special_requests = 0 and deposit_type = 'No Deposit' and lead_time >= 30 and replay_booking_date <= ? and replay_leave_books_date > ? and replay_arrival_date > ? and replay_arrival_date <= ? group by 1",
        [as_of, as_of, as_of + datetime.timedelta(days=14), as_of + datetime.timedelta(days=60)])
    out = []
    for r in books.to_dict("records"):
        part = resolved[resolved["hotel"] == r["hotel"]].set_index("engaged")
        if len(part) < 2 or int(part["n"].min()) < guests.min_rows or r["bookings"] < 20:
            continue
        gap = float(part.loc[0, "rate"] - part.loc[1, "rate"])
        if gap <= 0.05:
            continue
        out.append({"rule": "engagement", "hotel": r["hotel"], "date": "arrivals in 14 to 60 days", "priority": 3, "impact_eur": round(float(r["value"]) * gap * 0.2),
                    "title": str(int(r["bookings"])) + " flexible bookings at the " + r["hotel"] + " show no engagement, contact them before arrival",
                    "detail": "Bookings made a month or more ahead with no deposit cancel " + pct(float(part.loc[0, "rate"])) + " of the time when the guest made no request against " + pct(part.loc[1, "rate"]) + " with at least one (last 12 months, " + str(int(part["n"].sum())) + " resolved cases); " + eur(r["value"]) + " on the books; impact assumes a pre arrival contact closes a fifth of the gap",
                    "action": "Send a pre arrival email asking for preferences and arrival time, and offer a small upgrade for an early reconfirmation"})
    return out


# model and data hygiene
def rule_health():
    out = []
    drift = db.query_df("select count(*) filter (where status = 'alert') as alerts from monitoring.drift where run_at = (select max(run_at) from monitoring.drift)")
    perf = db.query_df("select roc_auc, brier, brier_base from monitoring.performance order by window_end desc limit 1")
    feeds = db.query_df("select feed from raw.feed_runs where run_at = (select max(run_at) from raw.feed_runs f2 where f2.feed = raw.feed_runs.feed) and status = 'error'")
    if len(perf) and float(perf["brier"][0]) >= float(perf["brier_base"][0]):
        out.append({"rule": "health", "hotel": "both", "date": "now", "priority": 1, "impact_eur": 0, "title": "Live cancellation predictions are no better than the base rate, retrain before acting on risk scores", "detail": "Brier " + str(perf["brier"][0]) + " against base " + str(perf["brier_base"][0]) + " on the last four weeks of arrivals", "action": "Run the weekly retrain job now"})
    if len(drift) and int(drift["alerts"][0]) >= 6:
        out.append({"rule": "health", "hotel": "both", "date": "now", "priority": 3, "impact_eur": 0, "title": str(int(drift["alerts"][0])) + " booking features drifted against last year, check the mix before trusting segment rules", "detail": "Population stability alerts on the last 30 days of new bookings", "action": "Review the drift chart on the models dashboard in Grafana"})
    for f in feeds["feed"].tolist():
        out.append({"rule": "health", "hotel": "both", "date": "now", "priority": 3, "impact_eur": 0, "title": "Feed " + f + " failed on its last run", "detail": "Demand signals depending on it are stale", "action": "Check the feed log"})
    return out


# run all rules on the live numbers
def compute(as_of=None):
    as_of = as_of or clock.today()
    fc = rm.forecast_by_date(as_of, 90)
    brief = rm.brief(as_of, 30)
    mix = rm.channel_mix(as_of, 90)
    elasticity = load_json("elasticity.json")
    causal = load_json("causal_channel.json")
    recs = []
    recs += rule_overbooking(fc, as_of)
    recs += rule_rate(fc, elasticity, as_of)
    recs += rule_stimulate(fc, brief, as_of)
    recs += rule_confirm(as_of)
    recs += rule_groups(fc, as_of)
    recs += rule_channel(mix, causal, as_of)
    recs += rule_agents(as_of)
    recs += rule_markets(as_of)
    recs += rule_min_stay(fc)
    recs += rule_accounts(as_of)
    recs += rule_history(as_of)
    recs += rule_engagement(as_of)
    recs += rule_health()
    for r in recs:
        r["rec_id"] = r["rule"] + ":" + r["hotel"] + ":" + str(r["date"])[:23]
        r["as_of"] = str(as_of)
    recs.sort(key=lambda r: (r["priority"], -r["impact_eur"]))
    return recs


# compute and store today's recommendations
def run():
    ensure_tables()
    as_of = clock.today()
    recs = compute(as_of)
    frame = pd.DataFrame([{"run_date": as_of, "rec_id": r["rec_id"], "rule": r["rule"], "hotel": r["hotel"], "priority": r["priority"], "impact_eur": float(r["impact_eur"]), "title": r["title"], "payload": json.dumps(r)} for r in recs])
    with db.db_lock:
        con = db.connect()
        try:
            con.execute("delete from recs.daily where run_date = ?", [as_of])
            if len(frame):
                con.register("incoming", frame)
                con.execute("insert into recs.daily select * from incoming")
        finally:
            con.close()
    print("recommendations stored " + str(len(frame)))
    return recs


# latest recommendations with what changed since the previous run
def latest():
    ensure_tables()
    runs = db.query_df("select distinct run_date from recs.daily order by run_date desc limit 2")["run_date"].tolist()
    if len(runs) == 0:
        return {"as_of": None, "recommendations": [], "new": [], "resolved": []}
    cur = db.query_df("select rec_id, payload from recs.daily where run_date = ?", [runs[0]])
    current = [json.loads(p) for p in cur["payload"]]
    new, resolved = [], []
    if len(runs) > 1:
        prev = db.query_df("select rec_id, payload from recs.daily where run_date = ?", [runs[1]])
        prev_ids = set(prev["rec_id"])
        cur_ids = set(cur["rec_id"])
        new = [r for r in current if r["rec_id"] not in prev_ids]
        resolved = [json.loads(p) for p in prev[~prev["rec_id"].isin(cur_ids)]["payload"]]
    total = sum(r["impact_eur"] for r in current)
    return {"as_of": str(runs[0])[:10], "previous_run": str(runs[1])[:10] if len(runs) > 1 else None, "total_impact_eur": round(total), "recommendations": current, "new": [r["rec_id"] for r in new], "resolved": resolved}
