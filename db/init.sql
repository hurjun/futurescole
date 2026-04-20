CREATE TABLE IF NOT EXISTS events (
    id          SERIAL PRIMARY KEY,
    event_type  VARCHAR(50)  NOT NULL,
    user_id     VARCHAR(36)  NOT NULL,
    session_id  VARCHAR(36)  NOT NULL,
    timestamp   TIMESTAMPTZ  NOT NULL,
    properties  JSONB        NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_event_type ON events (event_type);
CREATE INDEX IF NOT EXISTS idx_events_user_id    ON events (user_id);
CREATE INDEX IF NOT EXISTS idx_events_timestamp  ON events (timestamp);
