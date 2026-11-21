-- Breaker schema. Created by scripts/seed.py.
-- All timestamps are Gulf time (UTC+4) naive strings 'YYYY-MM-DD HH:MM:SS'.
CREATE TABLE reservations (
    id             TEXT PRIMARY KEY,
    api_key_id     TEXT NOT NULL,
    account_id     TEXT NOT NULL,
    beneficiary_id TEXT NOT NULL,
    amount_aed     REAL NOT NULL,
    purpose        TEXT,
    basis_id       TEXT,
    status         TEXT NOT NULL
                   CHECK (status IN ('pending', 'executed', 'held', 'released', 'rejected')),
    rule_id        TEXT,
    level          TEXT CHECK (level IN ('soft', 'hard')),
    detail         TEXT,
    payment_id     TEXT,
    created_at     TEXT NOT NULL,
    decided_at     TEXT,
    decided_by     TEXT
);

CREATE INDEX ix_reservations_key ON reservations (api_key_id, created_at);
CREATE INDEX ix_reservations_account ON reservations (account_id, created_at);

CREATE TABLE key_state (
    api_key_id  TEXT PRIMARY KEY,
    state       TEXT NOT NULL CHECK (state IN ('normal', 'paused', 'probe')),
    since       TEXT NOT NULL,
    incident_id TEXT
);

CREATE TABLE incidents (
    id                    TEXT PRIMARY KEY,
    api_key_id            TEXT NOT NULL,
    rule_id               TEXT NOT NULL,
    tripped_at            TEXT NOT NULL,
    first_violation_at    TEXT NOT NULL,
    verdict               TEXT,
    verdict_at            TEXT,
    pr_url                TEXT,
    status                TEXT NOT NULL DEFAULT 'open',
    violation_to_pause_ms INTEGER,
    trace_file            TEXT
);

CREATE TABLE jev_calls (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_id TEXT NOT NULL,
    request_json   TEXT,
    response_json  TEXT,
    latency_ms     INTEGER,
    released       INTEGER NOT NULL DEFAULT 0,
    mode           TEXT
);

CREATE TABLE events (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    at   TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL
);
