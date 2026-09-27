-- one row per booking with typed dates, stream dates and derived stay economics, from the synthetic stream or the real bookings
with base as (
    select
        booking_id,
        hotel,
        is_canceled,
        lead_time,
        make_date(arrival_date_year, extract(month from strptime(arrival_date_month, '%B')), arrival_date_day_of_month) as arrival_date,
        stays_in_weekend_nights,
        stays_in_week_nights,
        stays_in_weekend_nights + stays_in_week_nights as nights,
        adults,
        coalesce(children, 0) as children,
        babies,
        meal,
        coalesce(country, 'UNK') as country,
        market_segment,
        distribution_channel,
        is_repeated_guest,
        previous_cancellations,
        previous_bookings_not_canceled,
        reserved_room_type,
        assigned_room_type,
        booking_changes,
        deposit_type,
        coalesce(agent, 'NULL') as agent,
        coalesce(company, 'NULL') as company,
        days_in_waiting_list,
        customer_type,
        adr,
        required_car_parking_spaces,
        total_of_special_requests,
        reservation_status,
        reservation_status_date,
        {% if var('bookings_source') == 'synthetic' %}
        source_booking_id,
        shock
        {% else %}
        cast(null as bigint) as source_booking_id,
        '' as shock
        {% endif %}
    from {{ source(var('bookings_source'), 'bookings') }}
),
dated as (
    select
        *,
        arrival_date - to_days(lead_time) as booking_date,
        arrival_date + to_days(nights) as checkout_date,
        case when is_canceled = 1 then reservation_status_date else arrival_date + to_days(nights) end as leave_books_date,
        adr * nights as booking_value
    from base
),
-- bookings created the same day with the same hotel, arrival, length, rate, segment and agent, the block size
cloned as (
    select
        *,
        count(*) over (partition by hotel, booking_date, arrival_date, nights, adr, market_segment, agent) as same_day_clones
    from dated
),
-- resolved outcomes per agent and per country ordered by the date they left the books, for as of rates
agent_history as (
    select agent, leave_books_date as resolved_date,
        sum(1) over (partition by agent order by leave_books_date, booking_id rows unbounded preceding) as agent_prior_n,
        sum(is_canceled) over (partition by agent order by leave_books_date, booking_id rows unbounded preceding) as agent_prior_canceled
    from dated
),
country_history as (
    select country, leave_books_date as resolved_date,
        sum(1) over (partition by country order by leave_books_date, booking_id rows unbounded preceding) as country_prior_n,
        sum(is_canceled) over (partition by country order by leave_books_date, booking_id rows unbounded preceding) as country_prior_canceled
    from dated
),
with_agent as (
    select c.*, coalesce(a.agent_prior_n, 0) as agent_prior_n, coalesce(a.agent_prior_canceled, 0) as agent_prior_canceled
    from cloned c
    asof left join agent_history a on a.agent = c.agent and a.resolved_date < c.booking_date
),
with_country as (
    select w.*, coalesce(h.country_prior_n, 0) as country_prior_n, coalesce(h.country_prior_canceled, 0) as country_prior_canceled
    from with_agent w
    asof left join country_history h on h.country = w.country and h.resolved_date < w.booking_date
),
-- resolved outcomes per company ordered by the date they left the books, for as of rates
company_history as (
    select company, leave_books_date as resolved_date,
        sum(1) over (partition by company order by leave_books_date, booking_id rows unbounded preceding) as company_prior_n,
        sum(is_canceled) over (partition by company order by leave_books_date, booking_id rows unbounded preceding) as company_prior_canceled
    from dated
    where company <> 'NULL'
),
with_company as (
    select w.*, coalesce(ch.company_prior_n, 0) as company_prior_n, coalesce(ch.company_prior_canceled, 0) as company_prior_canceled
    from with_country w
    asof left join company_history ch on ch.company = w.company and ch.resolved_date < w.booking_date
),
-- the account and market activity known when the booking is made, previous bookings only
with_activity as (
    select
        *,
        datediff('day', lag(booking_date) over (partition by agent order by booking_date, booking_id), booking_date) as agent_recency_days,
        count(*) over (partition by agent order by booking_date range between interval 365 days preceding and interval 1 day preceding) as agent_prior_365,
        avg(lead_time) over (partition by agent order by booking_date range between interval 365 days preceding and interval 1 day preceding) as agent_avg_lead_365,
        avg(adr) over (partition by agent order by booking_date range between interval 365 days preceding and interval 1 day preceding) as agent_avg_adr_365,
        count(*) over (partition by country order by booking_date range between interval 365 days preceding and interval 1 day preceding) as country_prior_365,
        count(*) over (partition by hotel order by booking_date range between interval 365 days preceding and interval 1 day preceding) as hotel_prior_365
    from with_company
)
select
    *,
    round((company_prior_canceled + 20 * 0.37) / (company_prior_n + 20), 4) as company_prior_rate,
    leave_books_date + to_days({{ var('replay_shift_days') }}) <= current_date as outcome_known,
    round((agent_prior_canceled + 20 * 0.37) / (agent_prior_n + 20), 4) as agent_prior_rate,
    round((country_prior_canceled + 20 * 0.37) / (country_prior_n + 20), 4) as country_prior_rate,
    booking_date + to_days({{ var('replay_shift_days') }}) as replay_booking_date,
    arrival_date + to_days({{ var('replay_shift_days') }}) as replay_arrival_date,
    checkout_date + to_days({{ var('replay_shift_days') }}) as replay_checkout_date,
    leave_books_date + to_days({{ var('replay_shift_days') }}) as replay_leave_books_date
from with_activity
