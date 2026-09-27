-- one row per booking per stay night, used for occupancy and revenue by calendar date
with nights as (
    select
        booking_id,
        hotel,
        is_canceled,
        adr,
        market_segment,
        distribution_channel,
        customer_type,
        country,
        arrival_date,
        replay_arrival_date,
        nights
    from {{ ref('stg_bookings') }}
    where nights > 0
)
select
    n.booking_id,
    n.hotel,
    n.is_canceled,
    n.adr,
    n.market_segment,
    n.distribution_channel,
    n.customer_type,
    n.country,
    n.arrival_date + to_days(g.i) as stay_date,
    n.arrival_date + to_days(g.i) + to_days({{ var('replay_shift_days') }}) as replay_stay_date
from nights n
cross join lateral (select unnest(range(0, n.nights)) as i) g
