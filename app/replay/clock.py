import datetime
import pandas as pd
from app import db

# days added to booking dates by the dbt stream shift, 0 for the synthetic stream on the real calendar
replay_shift_days = 0

# columns needed for scoring, pulled from the staged bookings
booking_columns = "booking_id, hotel, is_canceled, lead_time, arrival_date, booking_date, leave_books_date, nights, stays_in_weekend_nights, stays_in_week_nights, adults, children, babies, meal, country, market_segment, distribution_channel, is_repeated_guest, previous_cancellations, previous_bookings_not_canceled, reserved_room_type, deposit_type, agent, company, days_in_waiting_list, customer_type, adr, required_car_parking_spaces, total_of_special_requests, booking_value, same_day_clones, agent_prior_rate, agent_prior_n, country_prior_rate, company_prior_rate, company_prior_n, agent_recency_days, agent_prior_365, agent_avg_lead_365, agent_avg_adr_365, country_prior_365, hotel_prior_365, outcome_known, replay_booking_date, replay_arrival_date, replay_leave_books_date"


# current replay date, the real calendar date in utc
def today():
    return datetime.datetime.now(datetime.timezone.utc).date()


# bookings on the books at a replay date, observed at that date
def on_books(as_of):
    sql = "select " + booking_columns + " from staging.stg_bookings where replay_booking_date <= ? and replay_leave_books_date > ?"
    frame = db.query_df(sql, [as_of, as_of])
    shift = pd.to_datetime(frame["replay_booking_date"]) - pd.to_datetime(frame["booking_date"])
    frame["as_of"] = pd.Timestamp(as_of) - shift
    return frame


# bookings created in a replay date window, observed at their booking date
def created_between(start, end):
    sql = "select " + booking_columns + " from staging.stg_bookings where replay_booking_date > ? and replay_booking_date <= ?"
    frame = db.query_df(sql, [start, end])
    frame["as_of"] = pd.to_datetime(frame["booking_date"])
    return frame


# bookings due to arrive before a replay date, a complete cohort with every outcome known
def resolved_before(as_of):
    sql = "select " + booking_columns + " from staging.stg_bookings where replay_arrival_date <= ?"
    frame = db.query_df(sql, [as_of])
    return frame


# convert a replay date to dataset time
def to_dataset_date(replay_date):
    shift = db.query_df("select (replay_booking_date - booking_date) as d from staging.stg_bookings limit 1")["d"][0]
    return pd.Timestamp(replay_date) - pd.Timedelta(shift)
