-- realised rooms sold, revenue, adr and occupancy per hotel per stay date
with capacity as (
    select 'Resort Hotel' as hotel, 260 as rooms
    union all
    select 'City Hotel' as hotel, 270 as rooms
),
agg as (
    select
        hotel,
        stay_date,
        replay_stay_date,
        count(*) filter (where is_canceled = 0) as rooms_sold,
        count(*) filter (where is_canceled = 1) as rooms_canceled,
        sum(adr) filter (where is_canceled = 0) as room_revenue
    from {{ ref('stg_stay_nights') }}
    group by 1, 2, 3
)
select
    a.hotel,
    a.stay_date,
    a.replay_stay_date,
    a.rooms_sold,
    a.rooms_canceled,
    round(a.room_revenue, 2) as room_revenue,
    c.rooms as capacity,
    round(a.rooms_sold / c.rooms, 4) as occupancy,
    round(a.room_revenue / nullif(a.rooms_sold, 0), 2) as adr,
    round(a.room_revenue / c.rooms, 2) as revpar
from agg a
join capacity c on c.hotel = a.hotel
