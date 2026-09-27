-- monthly revenue management kpis per hotel by arrival month
with b as (
    select
        hotel,
        date_trunc('month', arrival_date) as month,
        date_trunc('month', replay_arrival_date) as replay_month,
        count(*) as bookings,
        sum(is_canceled) as canceled,
        sum(case when is_canceled = 0 then nights else 0 end) as room_nights,
        sum(case when is_canceled = 0 then booking_value else 0 end) as revenue,
        sum(booking_value) as gross_booked_value,
        avg(lead_time) as avg_lead_time,
        avg(case when is_repeated_guest = 1 then 1.0 else 0.0 end) as repeat_share
    from {{ ref('stg_bookings') }}
    group by 1, 2, 3
),
occ as (
    select hotel, date_trunc('month', stay_date) as month, avg(occupancy) as occupancy, avg(revpar) as revpar
    from {{ ref('mart_daily_occupancy') }}
    group by 1, 2
)
select
    b.hotel,
    b.month,
    b.replay_month,
    b.bookings,
    b.canceled,
    round(b.canceled / b.bookings, 4) as cancel_rate,
    b.room_nights,
    round(b.revenue, 2) as revenue,
    round(b.gross_booked_value, 2) as gross_booked_value,
    round(b.revenue / nullif(b.room_nights, 0), 2) as adr,
    round(o.occupancy, 4) as occupancy,
    round(o.revpar, 2) as revpar,
    round(b.avg_lead_time, 1) as avg_lead_time,
    round(b.repeat_share, 4) as repeat_share
from b
left join occ o on o.hotel = b.hotel and o.month = b.month
