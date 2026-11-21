-- Toy bank schema. Created by scripts/seed.py.
CREATE TABLE clients (
    id   TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE accounts (
    id          TEXT PRIMARY KEY,
    client_id   TEXT NOT NULL REFERENCES clients(id),
    balance_aed REAL NOT NULL
);

CREATE TABLE api_keys (
    id         TEXT PRIMARY KEY,
    client_id  TEXT NOT NULL REFERENCES clients(id),
    secret     TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE beneficiaries (
    id          TEXT PRIMARY KEY,
    client_id   TEXT NOT NULL REFERENCES clients(id),
    name        TEXT NOT NULL,
    iban        TEXT NOT NULL,
    added_by    TEXT NOT NULL,
    approved_at TEXT
);

-- An approved reason to pay: invoice, payroll, loan.
CREATE TABLE bases (
    id             TEXT PRIMARY KEY,
    client_id      TEXT NOT NULL REFERENCES clients(id),
    beneficiary_id TEXT NOT NULL REFERENCES beneficiaries(id),
    type           TEXT NOT NULL CHECK (type IN ('invoice', 'payroll', 'loan')),
    amount_aed     REAL NOT NULL,
    approved       INTEGER NOT NULL DEFAULT 0,
    used           INTEGER NOT NULL DEFAULT 0,
    description    TEXT  -- what was approved, in words
);

-- Executed payments only.
CREATE TABLE payments (
    id             TEXT PRIMARY KEY,
    account_id     TEXT NOT NULL REFERENCES accounts(id),
    beneficiary_id TEXT NOT NULL REFERENCES beneficiaries(id),
    api_key_id     TEXT NOT NULL REFERENCES api_keys(id),
    amount_aed     REAL NOT NULL,
    purpose        TEXT,
    basis_id       TEXT,
    created_at     TEXT NOT NULL,
    -- Sent by the breaker (its reservation id); NULL for history rows.
    idempotency_key TEXT UNIQUE
);

CREATE INDEX ix_payments_beneficiary ON payments (beneficiary_id, created_at);
CREATE INDEX ix_payments_account ON payments (account_id, created_at);
CREATE INDEX ix_bases_beneficiary ON bases (beneficiary_id);
