-- 1. Event count by type
SELECT event_type, COUNT(*) AS cnt
FROM events
GROUP BY event_type
ORDER BY cnt DESC;

-- 2. Top 10 users by event count
SELECT user_id, COUNT(*) AS cnt
FROM events
GROUP BY user_id
ORDER BY cnt DESC
LIMIT 10;

-- 3. Hourly event distribution
SELECT DATE_TRUNC('hour', timestamp) AS hour, COUNT(*) AS cnt
FROM events
GROUP BY hour
ORDER BY hour;

-- 4. Error event ratio
SELECT
    COUNT(*)                                              AS total_events,
    COUNT(*) FILTER (WHERE event_type = 'error')         AS error_events,
    ROUND(
        COUNT(*) FILTER (WHERE event_type = 'error')
        * 100.0 / NULLIF(COUNT(*), 0),
        2
    )                                                     AS error_ratio_pct
FROM events;
