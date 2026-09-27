-- kpis per hotel and market segment and distribution channel
select
    hotel,
    market_segment,
    distribution_channel,
    count(*) as bookings,
    round(avg(is_canceled), 4) as cancel_rate,
    round(avg(adr), 2) as avg_adr,
    round(avg(lead_time), 1) as avg_lead_time,
    round(avg(nights), 2) as avg_nights,
    round(sum(case when is_canceled = 0 then booking_value else 0 end), 2) as revenue,
    round(avg(total_of_special_requests), 3) as avg_special_requests
from {{ ref('stg_bookings') }}
group by 1, 2, 3
