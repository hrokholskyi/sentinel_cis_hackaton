# Night Circuit Breaker

A smart rate limiter in front of a toy bank payment API.
It counts money and client behaviour, not requests, and puts suspicious payments on a reversible hold.
`SPEC.md` is the build spec.
All data is synthetic.

## Setup

```sh
uv venv --python 3.12 .venv
uv pip install -r requirements.txt
```

Run everything with `.venv/bin/python` (or activate the venv).

## Run

```sh
.venv/bin/python scripts/seed.py                      # create bank.db and breaker.db from scratch
.venv/bin/uvicorn bank.app:app --port 8001            # toy bank
.venv/bin/uvicorn breaker.app:app --port 8000         # breaker, clients only talk to this
.venv/bin/python scripts/traffic_legit.py             # 42 daytime payments, expect 42 executed
```

Each traffic script prints a one-line summary such as `sent 42 executed 42 held 0` and exits non-zero when the result differs from SPEC section 8.
Use `--delay <seconds>` to change the real pause between requests and `--trace <path>` to write the JSONL trace somewhere else.

Tests start both services on free ports against temp databases:

```sh
.venv/bin/python -m pytest -q
```

## Environment variables

See `.env.example` for all variables and their defaults.
The services do not read `.env` themselves; load it with `set -a; . ./.env; set +a` if you need other values.

| Variable | Default | Used by |
|---|---|---|
| `BANK_DB` | `bank.db` | bank, breaker (read-only attach), seed |
| `BREAKER_DB` | `breaker.db` | breaker, seed |
| `BANK_URL` | `http://127.0.0.1:8001` | breaker |
| `BREAKER_URL` | `http://127.0.0.1:8000` | traffic scripts |
| `DEMO_MODE` | `1` | bank, breaker: take "now" from the `X-Sim-Time` header |
| `DEMO_DATE` | `2026-11-21` | seed, traffic scripts |

## Choices left open by the spec

- Demo calendar is fixed on `DEMO_DATE` = D = 2026-11-21.
  Seed history covers the 90 days ending D-2, legit traffic runs on D-1 from 09:00, the attack runs on D from 02:00.
- All timestamps are stored as Gulf time (UTC+4, no DST) naive strings `YYYY-MM-DD HH:MM:SS`, so SQLite `datetime()` works on them.
  With `DEMO_MODE=1` (the default, so the plain run commands work) both services take "now" from `X-Sim-Time`; the breaker forwards that header to the bank.
  Without the header they use the real Gulf time.
- The seed is fully deterministic (fixed random seeds, fixed ids).
  Ids and the legit traffic plan live in `scripts/common.py`, shared by the seed and the traffic scripts.
- Legit plan: 40 invoice payments to 11 regular suppliers plus the first payments to the 2 new suppliers (AED 3,200 and 4,750), one every 13 minutes from 09:00 to 17:53.
  That gives at most 5 payments and under AED 150,000 in any rolling hour, about AED 520,000 for the day, and every amount is within the supplier's prior range and at most AED 25,000.
  `common.py` asserts this budget at import.
- Each legit payment has its own approved, unused invoice basis with the exact amount.
  Seed history payments each have their own approved, used basis.
- `ben_reg_05` (Falcon Cargo Handling) is reserved for the soft scenario: 14 prior payments, max exactly AED 52,000, last paid 31 days before D, and an approved unused basis `inv_4471` of AED 48,000.
  Legit traffic never pays it, so its facts match the SPEC section 7 example.
- The payroll set (30 employees, `ben_emp_01`..`ben_emp_30`) has salaries paid on the 25th of each month in the history window and one approved unused `payroll` basis for the current month each.
- The bank does not check bases, but when a payout with a `basis_id` executes it marks that basis `used = 1` (bookkeeping only).
- Payment and reservation ids are sequential (`pay_000001`, `res_000001`), assigned inside a `BEGIN IMMEDIATE` transaction.
- IBANs are synthetic strings `AE00` followed by digits; they are not valid IBANs on purpose.
- Traces: `request` is the JSON body only, never the key secret.
  `basis_id` is left out of the body when there is none.
