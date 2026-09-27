-- bookings created per booking date per hotel with lead time buckets and cancellation share
select
    hotel,
    booking_date,
    replay_booking_date,
    count(*) as bookings,
    sum(is_canceled) as canceled,
    round(avg(is_canceled), 4) as cancel_rate,
    round(avg(lead_time), 1) as avg_lead_time,
    round(avg(adr), 2) as avg_adr,
    round(sum(booking_value), 2) as booked_value,
    count(*) filter (where lead_time <= 7) as bookings_lead_0_7,
    count(*) filter (where lead_time between 8 and 30) as bookings_lead_8_30,
    count(*) filter (where lead_time between 31 and 90) as bookings_lead_31_90,
    count(*) filter (where lead_time > 90) as bookings_lead_90_plus
from {{ ref('stg_bookings') }}
group by 1, 2, 3
