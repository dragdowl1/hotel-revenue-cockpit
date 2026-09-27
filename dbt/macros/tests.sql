{% test dbt_utils_free_non_negative(model, column_name) %}
    select * from {{ model }} where {{ column_name }} < 0
{% endtest %}

{% test unique_hotel_stay_date(model) %}
    select hotel, stay_date, count(*) as n from {{ model }} group by 1, 2 having count(*) > 1
{% endtest %}
