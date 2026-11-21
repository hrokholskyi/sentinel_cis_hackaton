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
.venv/bin/python scripts/traffic_attack.py            # 02:00 attack, expect 6 executed, 36 held
```

`traffic_legit.py` rewrites `fixtures/legit.jsonl`.
`traffic_attack.py` rewrites `traces/attack.jsonl` (gitignored).
On the attack the breaker writes `fixtures/incidents/inc_0001.jsonl`.
Run the attack on a fresh seed (or after legit); a second attack run on the same databases only meets a paused key.

Breaker endpoints besides `POST /payouts`:

| Endpoint | What it does |
|---|---|
| `GET /health` | Health check on both services; the breaker also lists the loaded rule ids |
| `GET /state` | Key states, reservation counts and sums by status, open incident, all incidents, last 10 events |
| `POST /admin/keys/{id}/resume` | Key back to `normal`, closes its open incident |
| `POST /admin/holds/{id}/release` | Sends a held payment to the bank (marked `released`, then `executed`) |
| `POST /admin/holds/{id}/reject` | Marks a held payment `rejected`; it is never sent |

Admin endpoints need the header `X-Admin-Token: $ADMIN_TOKEN`.

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
| `RULES_DIR` | `rules` | breaker, loaded once at start |
| `INCIDENTS_DIR` | `fixtures/incidents` | breaker, incident trace files |
| `DEMO_MODE` | `1` | bank, breaker: take "now" from the `X-Sim-Time` header |
| `ADMIN_TOKEN` | `dev-admin-token` | breaker admin endpoints |
| `SOFT_MIN_AMOUNT_AED` | `5000` | rules, bound as `:soft_min_amount_aed` |
| `HOLD_TTL_MIN` | `30` | not used before milestone 6 |
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
  `status` is the reservation outcome (`executed`, `held` or `rejected`).

### Rules and counters

- Counters include the current payment.
  Inside one `BEGIN IMMEDIATE` transaction on `breaker.db` the reservation is inserted as `pending`, all rules run, and it becomes `held` on any violation.
  So `COUNT(*) > 8` fires on the 9th reservation in the window.
- The paused-key check runs inside the same transaction, so two requests cannot race past a pause.
- Every time window has an upper bound: `created_at >= datetime(:now, '-5 minutes') AND created_at <= :now`.
  The demo clock goes backwards (legit on D-1, soft at D 02:10, attack at D 02:00), and later rows must not count in earlier windows.
  "Prior payments" of a beneficiary are bank payments with `created_at < :now`.
- Rules run on the breaker connection with `bank.db` attached read-only as schema `bank`; history comes from `bank.payments`, counters from `reservations`.
- Rule files are named `NN_<rule_id>.sql` so file-name order is the table order in SPEC section 6.
  Engine params are the ones in the SPEC header plus config values such as `:soft_min_amount_aed`.
- A rule that errors, returns no row, or returns `violated` NULL counts as violated (fail closed), with the error in `detail`.
  A rule file without valid `-- id:` and `-- level:` headers stops the breaker from starting.
- When several rules fire, the reservation stores the first hard rule if any, otherwise the first soft rule (`rule_id`, `level`, `detail`).
- `drain_1h` compares the sum reserved in the last hour with 30% of the balance at the start of that hour, taken as the current balance plus payments executed in the window.
- `off_hours_amount` treats 07:00:00 to 20:00:00 inclusive as business hours.
- There is deliberately no rule for dormant beneficiaries (SPEC section 6).

### Holds, pause and incidents

- Responses: an executed payment returns the bank's 200 response with `reservation_id` added.
  A held one returns 202 `{"status":"held","reason":...,"reservation_id":...,"rule_id":...,"level":...,"detail":...}`.
  `reason` is `soft_stop`, `hard_stop` or `key_paused`.
- Holds on a paused key are stored with `rule_id = key_paused`, `level = hard`; rules are not evaluated for them.
- If the bank rejects a forwarded payment, the reservation becomes `rejected` and the bank's error is returned.
  If the bank cannot be reached, the outcome is unclear, so the reservation stays `held` with `rule_id = bank_unavailable` and the breaker returns 502.
- Only soft violations: `decide_soft()` in `breaker/app.py` decides.
  Until milestone 3 it always keeps the hold, and an event "client asked to confirm payment" is logged.
- On the first hard violation of a key, `inc_0001` (then `inc_0002`, ...) is created in the same transaction, the key is paused, and the key's requests from the last hour (bounded above by `now`) are written to `INCIDENTS_DIR/<id>.jsonl` in the traffic trace format.
  Every later request of that key is appended while the incident is open.
  One open incident per key: a later hard violation attaches to it.
- `incidents` has two extra columns: `trace_file`, and `violation_to_pause_ms`.
  `first_violation_at` is the request time of the first request in that hour that violated any rule (payment 7 in the demo).
  `violation_to_pause_ms` is the request-clock gap from `first_violation_at` to the tripping request, plus the real processing time of the tripping request (about 4,000 ms in the demo).
- `reservations` has two extra columns: `detail` (the stored rule's detail) and `payment_id` (the bank payment id).
- Admin resume closes the key's open incident (`status = closed`).
  Admin release does not re-check rules; it is an explicit human override.
- Trace file writes run inside the `BEGIN IMMEDIATE` transaction, so lines are in commit order; a failed write logs an `error` event and never undoes a hold.
- `fixtures/legit.jsonl` and `fixtures/incidents/inc_0001.jsonl` are committed from a clean run (fresh seed, legit, attack), for the replay script and the investigator.
