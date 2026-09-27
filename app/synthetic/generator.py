import heapq
import hashlib
import datetime
import numpy as np
import pandas as pd
from app.synthetic import pool as pool_module
from app.synthetic import signals
from app.synthetic import outcomes

# generation parameters
domestic_holiday_lift = 1.3
family_school_lift = 1.25
hot_dry_last_minute_lift = 1.2
rainy_last_minute_cut = 0.85
storm_flip_share = 0.08
lead_jitter_sigma = 0.15
adr_jitter_sigma = 0.05
nights_change_share = 0.15
requests_change_share = 0.12
adults_change_share = 0.05
flexibility = {"No Deposit": 1.0, "Refundable": 0.6, "Non Refund": 0.2}
max_occupancy = 0.98
overbook_buffer = 0.02


# deterministic random generator for one hotel and arrival week
def week_rng(seed, hotel, week_start):
    key = str(seed) + "|" + hotel + "|" + str(week_start.date())
    return np.random.default_rng(int(hashlib.sha256(key.encode()).hexdigest()[:16], 16))


# everything the generator needs, loaded once
class World:
    def __init__(self, horizon):
        self.pool = pool_module.load_pool()
        self.weekly = pool_module.weekly_profile(self.pool)
        self.dow = pool_module.dow_profile(self.pool)
        self.level = signals.level_index(horizon)
        self.price = {hotel: signals.rate_index(horizon, hotel) for hotel in pool_module.capacity}
        segments = self.pool["market_segment"].value_counts(normalize=True)
        self.base_direct_share = float(segments.get("Direct", 0.0) + segments.get("Corporate", 0.0))
        self.agency_share = float(segments.get("Online TA", 0.0) + segments.get("Offline TA/TO", 0.0))
        self.attention = signals.attention_index()
        self.fx = signals.fx_index(horizon)
        self.public_holidays, self.school_holidays = signals.holiday_sets()
        self.weather = signals.weather_flags()
        self.events = signals.load_events()
        self.buckets = {key: part.index.values for key, part in self.pool.groupby(["hotel", "arrival_month"])}
        self.lead_cache = {}
        self.outcome_model = outcomes.load_world_model(self.pool)
        self.cancel_offsets = {hotel: part.loc[part["is_canceled"] == 1, "cancel_offset"].values for hotel, part in self.pool.groupby("hotel")}
        cancelled = self.pool[self.pool["is_canceled"] == 1]
        self.noshow_share = {dep: float((part["reservation_status"] == "No-Show").mean()) for dep, part in cancelled.groupby("deposit_type")}
        self.mean_flexibility = {hotel: float(part["deposit_type"].map(flexibility).fillna(0.5).mean()) for hotel, part in self.pool.groupby("hotel")}
        self.iso2 = {"PRT": "PT", "GBR": "GB", "FRA": "FR", "ESP": "ES", "DEU": "DE", "ITA": "IT", "IRL": "IE", "BEL": "BE", "BRA": "BR", "NLD": "NL", "USA": "US", "CHE": "CH", "CHN": "CN", "AUT": "AT", "SWE": "SE"}

    # value of a monthly index for a date, the last value beyond the end
    def monthly(self, series, date):
        month = pd.Timestamp(date.year, date.month, 1)
        if month in series.index:
            return float(series[month])
        return float(series.iloc[-1])

    # events active for one hotel on one date with their weights
    def active_events(self, hotel, date):
        out = []
        for _, event in self.events.iterrows():
            if event["region"] not in ("both", pool_module.hotel_region[hotel]):
                continue
            w = signals.event_weight(event, date)
            if w > 0:
                out.append((event, w))
        return out


# demand multiplier of the events that touch every market, one number per hotel and week before renormalisation
def event_demand(world, hotel, week_start):
    mid = week_start + pd.Timedelta(days=3)
    mult = 1.0
    for event, w in world.active_events(hotel, mid):
        if len(event["markets"]) > 0:
            continue
        mult = mult * (1.0 + w * (event["demand_multiplier"] - 1.0))
    return mult


# market and segment multipliers of the active events for one hotel and week
def event_market_multipliers(world, hotel, week_start):
    mid = week_start + pd.Timedelta(days=3)
    markets = {}
    segments = {}
    for event, w in world.active_events(hotel, mid):
        for country in event["markets"]:
            markets[country] = markets.get(country, 1.0) * (1.0 + w * (event["demand_multiplier"] - 1.0))
        for segment in event["segment_filter"]:
            segments[segment] = segments.get(segment, 1.0) * (1.0 + w * (event["demand_multiplier"] - 1.0))
    return markets, segments


# lead time factor of the active events for bookings made around one date, cached per hotel and date
def event_lead_factor(world, hotel, date):
    key = (hotel, date.date())
    if key in world.lead_cache:
        return world.lead_cache[key]
    factor = 1.0
    for event, w in world.active_events(hotel, date):
        factor = factor * (1.0 + w * (event["lead_factor"] - 1.0))
    world.lead_cache[key] = factor
    return factor


# share of the bookings on the books that a flip event is expected to cancel in one week, flip share times the mean deposit flexibility of the atoms
def expected_flip(world, hotel, week_start):
    mid = week_start + pd.Timedelta(days=3)
    share = 0.0
    for event, w in world.active_events(hotel, mid):
        if event["cancel_flip_share"] <= 0 or mid < event["start_date"] or mid > event["end_date"]:
            continue
        if len(event["markets"]) > 0 or len(event["segment_filter"]) > 0:
            continue
        share = max(share, float(event["cancel_flip_share"]) * world.mean_flexibility[hotel])
    return min(share, 0.9)


# target gross bookings per hotel and week from the real weekly profile, the eurostat level of realised nights grossed up by the expected flips, and the event multipliers renormalised per month
def weekly_targets(world, weeks):
    rows = []
    for hotel in pool_module.capacity:
        for week_start in weeks:
            woy = min(int(week_start.isocalendar()[1]), 52)
            base = float(world.weekly[hotel][woy - 1])
            level = world.monthly(world.level, week_start + pd.Timedelta(days=3))
            gross_up = 1.0 / (1.0 - expected_flip(world, hotel, week_start))
            rows.append({"hotel": hotel, "week_start": week_start, "month": pd.Timestamp(week_start.year, week_start.month, 1), "base": base * level * gross_up, "event": event_demand(world, hotel, week_start)})
    frame = pd.DataFrame(rows)
    weighted = frame.groupby("month").apply(lambda p: float(np.average(p["event"], weights=p["base"])) if p["base"].sum() > 0 else 1.0)
    frame["event_norm"] = frame["event"] / frame["month"].map(weighted)
    frame["target"] = frame["base"] * frame["event_norm"]
    return frame


# sampling weights of the atoms of one hotel and month for one arrival week, markets, calendar, weather and events
def atom_weights(world, atoms, hotel, week_start):
    weights = np.ones(len(atoms))
    attention_week = week_start - pd.Timedelta(weeks=6)
    att_row = world.attention.reindex([attention_week], method="nearest").iloc[0]
    fx_month = pd.Timestamp(attention_week.year, attention_week.month, 1)
    fx_row = world.fx.reindex([fx_month], method="nearest").iloc[0]
    markets, segments = event_market_multipliers(world, hotel, week_start)
    groups = atoms["group"].values
    countries = atoms["country"].values
    for g in signals.attention_groups:
        weights[groups == g] = weights[groups == g] * float(att_row.get(g, 1.0))
    for country, currency in pool_module.country_currency.items():
        if currency in fx_row.index:
            weights[countries == country] = weights[countries == country] * float(fx_row[currency]) ** signals.fx_elasticity
    for country, m in markets.items():
        weights[countries == country] = weights[countries == country] * m
    seg = atoms["market_segment"].values
    for segment, m in segments.items():
        weights[seg == segment] = weights[seg == segment] * m
    days = [week_start + pd.Timedelta(days=i) for i in range(7)]
    pt_holiday = any(d in world.public_holidays.get("PT", set()) for d in days)
    if pt_holiday:
        weights[countries == "PRT"] = weights[countries == "PRT"] * domestic_holiday_lift
    family = atoms["family"].values == 1
    for country, iso2 in world.iso2.items():
        school = world.school_holidays.get(iso2, set())
        if len(school) > 0 and any(d in school for d in days):
            mask = family & (countries == country)
            weights[mask] = weights[mask] * family_school_lift
    location = pool_module.hotel_region[hotel]
    flags = world.weather.reindex([(location, d) for d in days]).fillna(0)
    last_minute = atoms["lead_time"].values <= 7
    if flags["hot_dry"].mean() >= 0.5:
        weights[last_minute] = weights[last_minute] * hot_dry_last_minute_lift
    if flags["rainy"].mean() >= 0.5:
        weights[last_minute] = weights[last_minute] * rainy_last_minute_cut
    return weights / weights.sum()


# stay nights split into weekend and week nights from the arrival weekday
def split_nights(arrival, nights):
    dows = (arrival.dt.dayofweek.values[:, None] + np.arange(int(nights.max()))[None, :]) % 7
    inside = np.arange(int(nights.max()))[None, :] < nights.values[:, None]
    weekend = ((dows == 4) | (dows == 5)) & inside
    return weekend.sum(axis=1), nights.values - weekend.sum(axis=1)


# generate the bookings of one hotel and arrival week from the atoms of the same calendar month
def generate_week(world, hotel, week_start, target, seed):
    rng = week_rng(seed, hotel, week_start)
    n = int(rng.poisson(max(target, 0.0)))
    if n == 0:
        return None
    month = (week_start + pd.Timedelta(days=3)).month
    idx = world.buckets.get((hotel, month))
    atoms = world.pool.loc[idx]
    weights = atom_weights(world, atoms, hotel, week_start)
    picks = atoms.iloc[rng.choice(len(atoms), size=n, replace=True, p=weights)].reset_index(drop=True)
    dow = rng.choice(7, size=n, p=world.dow[hotel])
    out = picks.copy()
    out["arrival_date"] = week_start + pd.to_timedelta(dow, unit="D")
    jitter = np.exp(rng.normal(0, lead_jitter_sigma, size=n))
    lead_factor = np.array([event_lead_factor(world, hotel, a - pd.Timedelta(days=int(l))) for a, l in zip(out["arrival_date"], picks["lead_time"])])
    out["lead_time"] = np.round(picks["lead_time"].values * jitter * lead_factor).astype(int).clip(0, 700)
    out["booking_date"] = out["arrival_date"] - pd.to_timedelta(out["lead_time"], unit="D")
    out["adr"] = (picks["adr"].values * world.monthly(world.price[hotel], week_start)).round(2)
    out["source_booking_id"] = picks["booking_id"].values
    convert_channel(world, out, week_start, rng)
    augment(out, rng)
    weekend, week = split_nights(out["arrival_date"], out["nights"])
    out["stays_in_weekend_nights"] = weekend
    out["stays_in_week_nights"] = week
    out["arrival_month"] = out["arrival_date"].dt.month
    out["arrival_dow"] = out["arrival_date"].dt.dayofweek
    draw_outcomes(world, out, hotel, rng)
    out["shock"] = ""
    apply_shocks(world, out, hotel, rng)
    out["reservation_status_date"] = np.where(out["is_canceled"] == 1, out["arrival_date"] - pd.to_timedelta(out["cancel_offset"], unit="D"), out["arrival_date"] + pd.to_timedelta(out["nights"], unit="D"))
    out["reservation_status_date"] = pd.to_datetime(out["reservation_status_date"])
    return out


# the channel mix of the market: the source hotels sold 82 percent of their bookings through agencies, while four star European hotels take about a third of their bookings direct today; a share of the agency atoms becomes direct bookings, the share rising linearly from the source data's own level in 2017 to the benchmark in 2023 and flat afterwards
direct_target_share = 0.33
direct_ramp_start = 2017
direct_ramp_end = 2023


def direct_conversion(world, year):
    target = world.base_direct_share + (direct_target_share - world.base_direct_share) * min(max((year - direct_ramp_start) / (direct_ramp_end - direct_ramp_start), 0.0), 1.0)
    return max(0.0, (target - world.base_direct_share) / max(world.agency_share, 1e-6))


def convert_channel(world, out, week_start, rng):
    share = direct_conversion(world, week_start.year)
    agency = out["market_segment"].isin(["Online TA", "Offline TA/TO"]).values
    flip = agency & (rng.random(len(out)) < share)
    out.loc[flip, "market_segment"] = "Direct"
    out.loc[flip, "distribution_channel"] = "Direct"
    out.loc[flip, "agent"] = "NULL"
    return out


# small random changes to the copied atom so that no two synthetic bookings share an exact signature: rate noise, one night more or less, one request more or less, one adult more or less
def augment(out, rng):
    n = len(out)
    out["adr"] = (out["adr"].values * np.exp(rng.normal(0, adr_jitter_sigma, size=n))).round(2)
    step = rng.choice([-1, 1], size=n)
    change = rng.random(n) < nights_change_share
    out["nights"] = np.where(change, np.maximum(out["nights"].values + step, 1), out["nights"].values)
    change = rng.random(n) < requests_change_share
    out["total_of_special_requests"] = np.where(change, np.clip(out["total_of_special_requests"].values + step, 0, 5), out["total_of_special_requests"].values)
    change = rng.random(n) < adults_change_share
    out["adults"] = np.where(change, np.clip(out["adults"].values + step, 1, 4), out["adults"].values)
    return out


# cancel outcome drawn from the world model probability of the synthetic booking, the atom's own outcome is never copied; cancellation dates from the atom when it cancelled, otherwise from the hotel's distribution of cancellation offsets; a share of the cancellations by deposit type, taken from the real data, are no shows on the arrival day
def draw_outcomes(world, out, hotel, rng):
    p = outcomes.cancel_probability(world.outcome_model, out, out["source_booking_id"].values)
    out["is_canceled"] = (rng.random(len(out)) < p).astype(int)
    atom_offset = out["cancel_offset"].values
    drawn = rng.choice(world.cancel_offsets[hotel], size=len(out))
    offset = np.where(atom_offset > 0, atom_offset, drawn)
    out["cancel_offset"] = np.where(out["is_canceled"].values == 1, np.minimum(offset, out["lead_time"].values), 0)
    share = out["deposit_type"].astype(str).map(world.noshow_share).fillna(0.0).values
    noshow = (out["is_canceled"].values == 1) & (rng.random(len(out)) < share)
    out.loc[noshow, "cancel_offset"] = 0
    out["reservation_status"] = np.where(noshow, "No-Show", np.where(out["is_canceled"].values == 1, "Canceled", "Check-Out"))
    return out


# the stop sell rule of the hotel: a booking is accepted only while the rooms expected to stay on every night of it, the bookings on the books at that moment less the cancellations the world model expects among them, stay within capacity plus a small overbooking buffer; refused bookings are the demand the hotel turned away, protected rows (bookings already made) are always kept
def accept_bookings(world, frame, protected=None):
    frame = frame.reset_index(drop=True)
    work = frame.copy()
    work["arrival_month"] = work["arrival_date"].dt.month
    work["arrival_dow"] = work["arrival_date"].dt.dayofweek
    if "nights" not in work.columns:
        work["nights"] = work["stays_in_weekend_nights"] + work["stays_in_week_nights"]
    p_all = outcomes.cancel_probability(world.outcome_model, work, work["source_booking_id"].values)
    leave_all = pd.to_datetime(work["reservation_status_date"]).values.astype("datetime64[D]").astype(np.int64)
    arrival_all = work["arrival_date"].values.astype("datetime64[D]").astype(np.int64)
    booked_all = work["booking_date"].values.astype("datetime64[D]").astype(np.int64)
    nights_all = work["nights"].values.astype(int)
    cancelled_all = work["is_canceled"].values == 1
    protected_all = np.zeros(len(work), dtype=bool) if protected is None else np.asarray(protected, dtype=bool)
    keep = np.ones(len(work), dtype=bool)
    refused = {}
    for hotel, cap in pool_module.capacity.items():
        rows = np.where(work["hotel"].values == hotel)[0]
        rows = rows[np.lexsort((arrival_all[rows], booked_all[rows]))]
        if len(rows) == 0:
            continue
        origin = int(min(booked_all[rows].min(), arrival_all[rows].min()))
        span = int((arrival_all[rows] + nights_all[rows]).max() - origin) + 2
        gross = np.zeros(span)
        expected = np.zeros(span)
        limit = cap * (1.0 + overbook_buffer)
        releases = []
        refused[hotel] = 0
        for i in rows:
            day = int(booked_all[i] - origin)
            while releases and releases[0][0] <= day:
                _, j = heapq.heappop(releases)
                a, b = int(arrival_all[j] - origin), int(arrival_all[j] - origin) + nights_all[j]
                gross[a:b] -= 1
                expected[a:b] -= p_all[j]
            a, b = int(arrival_all[i] - origin), int(arrival_all[i] - origin) + nights_all[i]
            if not protected_all[i] and np.any(gross[a:b] - expected[a:b] + (1.0 - p_all[i]) > limit):
                keep[i] = False
                refused[hotel] += 1
                continue
            gross[a:b] += 1
            expected[a:b] += p_all[i]
            if cancelled_all[i]:
                heapq.heappush(releases, (int(leave_all[i] - origin), i))
    return frame[keep].reset_index(drop=True), refused


# sudden onset events cancel bookings on the books whose stay falls inside the window, hazard by flexibility and distance, and turn arrivals on disruption days into no shows
def apply_shocks(world, out, hotel, rng):
    for _, event in world.events.iterrows():
        if event["region"] not in ("both", pool_module.hotel_region[hotel]):
            continue
        if event["cancel_flip_share"] <= 0 and event["noshow_share"] <= 0:
            continue
        start = event["start_date"]
        end = event["end_date"]
        checkout = out["arrival_date"] + pd.to_timedelta(out["nights"], unit="D")
        overlap = (out["arrival_date"] <= end) & (checkout > start)
        if len(event["markets"]) > 0:
            overlap = overlap & out["country"].isin(event["markets"])
        if len(event["segment_filter"]) > 0:
            overlap = overlap & out["market_segment"].isin(event["segment_filter"])
        on_books = (out["booking_date"] < start) & (out["is_canceled"] == 0) & overlap
        if event["cancel_flip_share"] > 0 and on_books.any():
            distance = (out.loc[on_books, "arrival_date"] - start).dt.days.clip(lower=0).values
            decay = 0.5 ** (distance / max(event["recovery_half_life_days"], 1))
            flex = out.loc[on_books, "deposit_type"].map(flexibility).fillna(0.5).values
            p = event["cancel_flip_share"] * flex * decay
            flip = rng.random(len(p)) < p
            rows = out.index[on_books][flip]
            out.loc[rows, "is_canceled"] = 1
            out.loc[rows, "reservation_status"] = "Canceled"
            gap = (out.loc[rows, "arrival_date"] - start).dt.days.clip(lower=0)
            out.loc[rows, "cancel_offset"] = np.minimum(gap.values - rng.integers(0, 4, size=len(rows)).clip(0), out.loc[rows, "lead_time"].values).clip(0)
            out.loc[rows, "shock"] = event["event_id"]
        arrives = (out["arrival_date"] >= start) & (out["arrival_date"] <= end) & (out["is_canceled"] == 0)
        if event["noshow_share"] > 0 and arrives.any():
            hit = rng.random(int(arrives.sum())) < event["noshow_share"]
            rows = out.index[arrives][hit]
            out.loc[rows, "is_canceled"] = 1
            out.loc[rows, "reservation_status"] = "No-Show"
            out.loc[rows, "cancel_offset"] = 0
            out.loc[rows, "shock"] = event["event_id"]
    location = pool_module.hotel_region[hotel]
    stormy = world.weather.reindex([(location, d) for d in out["arrival_date"]])["storm"].fillna(0).values == 1
    candidates = stormy & (out["is_canceled"].values == 0) & (out["deposit_type"].values == "No Deposit")
    if candidates.any():
        hit = rng.random(int(candidates.sum())) < storm_flip_share
        rows = out.index[candidates][hit]
        out.loc[rows, "is_canceled"] = 1
        out.loc[rows, "reservation_status"] = "Canceled"
        out.loc[rows, "cancel_offset"] = np.minimum(rng.integers(0, 3, size=len(rows)), out.loc[rows, "lead_time"].values)
        out.loc[rows, "shock"] = "storm"


# drop the latest booked stayed bookings on nights where the hotel would be over capacity, the guests the hotel had to turn away
def enforce_capacity(frame):
    keep = np.ones(len(frame), dtype=bool)
    for hotel, cap in pool_module.capacity.items():
        limit = int(cap * max_occupancy)
        part = frame.loc[(frame["hotel"] == hotel) & (frame["is_canceled"] == 0), ["arrival_date", "booking_date", "nights"]]
        nights = part.loc[part.index.repeat(part["nights"].astype(int))]
        nights = nights.assign(stay_date=nights["arrival_date"] + pd.to_timedelta(nights.groupby(level=0).cumcount(), unit="D"))
        nights = nights.sort_values(["stay_date", "booking_date"])
        removed = set()
        for stay_date, group in nights.groupby("stay_date", sort=True):
            if len(group) <= limit:
                continue
            alive = group.index[~group.index.isin(list(removed))]
            excess = len(alive) - limit
            if excess > 0:
                latest = group.loc[alive].sort_values("booking_date", ascending=False).index[:excess]
                removed.update(latest.tolist())
        keep[frame.index.isin(list(removed))] = False
    return frame[keep].reset_index(drop=True)


# generate every arrival week between two dates for both hotels and assemble the synthetic bookings table
def generate(world, start, end, seed=7):
    weeks = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="W-MON")
    targets = weekly_targets(world, weeks)
    parts = []
    for row in targets.itertuples(index=False):
        part = generate_week(world, row.hotel, row.week_start, row.target, seed)
        if part is not None:
            parts.append(part)
    frame = pd.concat(parts, ignore_index=True)
    frame, refused = accept_bookings(world, frame)
    print("bookings turned away by the stop sell rule: " + ", ".join(h + " " + str(n) for h, n in refused.items()))
    frame = enforce_capacity(frame)
    frame = frame.sort_values(["booking_date", "arrival_date", "hotel"]).reset_index(drop=True)
    frame["booking_id"] = np.arange(1, len(frame) + 1)
    frame["arrival_date_year"] = frame["arrival_date"].dt.year
    frame["arrival_date_month"] = frame["arrival_date"].dt.month.map(lambda m: pool_module.month_names[m - 1])
    frame["arrival_date_week_number"] = frame["arrival_date"].dt.isocalendar().week.astype(int)
    frame["arrival_date_day_of_month"] = frame["arrival_date"].dt.day
    frame["generated_at"] = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    return frame[output_columns()], targets


# columns of the synthetic table, the raw csv layout plus provenance
def output_columns():
    return ["booking_id", "hotel", "is_canceled", "lead_time", "arrival_date_year", "arrival_date_month", "arrival_date_week_number", "arrival_date_day_of_month", "stays_in_weekend_nights", "stays_in_week_nights", "adults", "children", "babies", "meal", "country", "market_segment", "distribution_channel", "is_repeated_guest", "previous_cancellations", "previous_bookings_not_canceled", "reserved_room_type", "assigned_room_type", "booking_changes", "deposit_type", "agent", "company", "days_in_waiting_list", "customer_type", "adr", "required_car_parking_spaces", "total_of_special_requests", "reservation_status", "reservation_status_date", "source_booking_id", "shock", "generated_at"]
