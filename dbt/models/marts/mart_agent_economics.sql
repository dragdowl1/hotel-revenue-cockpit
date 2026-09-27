-- partner economics per booking agent, the b2b supply view
with a as (
    select
        agent,
        count(*) as bookings,
        count(distinct hotel) as hotels,
        min(booking_date) as first_booking_date,
        max(booking_date) as last_booking_date,
        round(avg(is_canceled), 4) as cancel_rate,
        round(avg(adr), 2) as avg_adr,
        round(avg(lead_time), 1) as avg_lead_time,
        round(sum(case when is_canceled = 0 then booking_value else 0 end), 2) as realised_revenue,
        round(sum(case when is_canceled = 1 then booking_value else 0 end), 2) as lost_value
    from {{ ref('stg_bookings') }}
    group by 1
)
select
    *,
    round(realised_revenue / bookings, 2) as revenue_per_booking,
    datediff('day', first_booking_date, last_booking_date) as active_days
from a
