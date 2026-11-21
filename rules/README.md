# Rules

Every file `rules/*.sql` is one rule of layer 1 of the breaker.
The breaker loads all of them once at start, in file-name order, and runs every rule on every payout request.
Adding a file adds a rule; no code change is needed.
Restart the breaker to load a new or changed rule.

## File name

Name the file `NN_<rule_id>.sql`, for example `04_burst_count_5m.sql`.
`NN` is a two-digit number; rules are evaluated in file-name order, so a new rule takes the next free number.
`<rule_id>` is the same id as in the `-- id:` header.

## File format

A rule file is header comments followed by exactly one `SELECT`.

```sql
-- id: burst_count_5m
-- level: hard
-- description: more payments from one API key in 5 minutes than its limit
-- params: :api_key_id :account_id :beneficiary_id :amount_aed :now :basis_id
SELECT COUNT(*) > 8 AS violated,
       'payments in 5 min: ' || COUNT(*) AS detail
FROM reservations
WHERE api_key_id = :api_key_id
  AND status IN ('pending','executed','held','released')
  AND created_at >= datetime(:now, '-5 minutes')
  AND created_at <= :now;
```

Headers, each on its own line in the form `-- name: value`:

| Header | Required | Meaning |
|---|---|---|
| `id` | yes | Unique rule id, letters, digits and `_`. It is stored on held payments and shown on the dashboard. |
| `level` | yes | `soft` or `hard` (see below). |
| `description` | no, but write one | One line in plain English: when the rule is violated. |
| `params` | no | The parameters the rule uses, for the reader. The engine binds all parameters to every rule anyway. |

A file without a valid `id` and `level`, or with an id that another file already uses, stops the breaker from starting.

The `SELECT` must return exactly one row with two columns:

- `violated`: 1 when the rule is violated, 0 when it is not.
- `detail`: short text that explains the numbers, for example `'payments in 5 min: 9'`.
  It is stored on the held payment and shown to the investigator.

An aggregate without `GROUP BY` (`COUNT`, `SUM`, `MAX`) always returns exactly one row, so it is the safest shape.
Use `COALESCE` so that an empty set does not make `violated` NULL.

## Bound parameters

| Parameter | Type | Value |
|---|---|---|
| `:api_key_id` | text | Id of the API key that sent the payout, for example `key_microsoft_1`. |
| `:account_id` | text | Account to debit. |
| `:beneficiary_id` | text | Beneficiary to pay. |
| `:amount_aed` | real | Amount of the payment in AED. |
| `:now` | text | Time of the request, `YYYY-MM-DD HH:MM:SS`. |
| `:basis_id` | text or NULL | Basis id sent with the payment, NULL when there is none. |
| `:soft_min_amount_aed` | real | Config `SOFT_MIN_AMOUNT_AED` (default 5000). |

Thresholds belong in config or in the rule file, never in a model.
If a new threshold should be configurable, it needs a config value in `breaker/app.py` (`RULE_CONFIG`); a number in the rule file is fine otherwise.

## Tables a rule may read

Rules run on the breaker database, with the bank database attached read-only as schema `bank`.
Read `reservations` and the `bank.*` tables only.

`reservations` (the breaker's counters, one row per payout request):

| Column | Meaning |
|---|---|
| `id` | Reservation id, `res_000001`, ... |
| `api_key_id`, `account_id`, `beneficiary_id` | Who pays whom. |
| `amount_aed`, `purpose`, `basis_id` | What the client sent. |
| `status` | `pending`, `executed`, `held`, `released` or `rejected`. |
| `rule_id`, `level`, `detail` | The rule that held it, if any. |
| `created_at` | Request time. |
| `decided_at`, `decided_by`, `payment_id`, `incident_id` | Outcome bookkeeping. |

Counters must include held and pending payments, not only executed ones.
Count `status IN ('pending','executed','held','released')`; `rejected` payments were never sent.

Bank tables (history and master data):

| Table | Columns |
|---|---|
| `bank.clients` | `id`, `name` |
| `bank.accounts` | `id`, `client_id`, `balance_aed` (current balance) |
| `bank.api_keys` | `id`, `client_id`, `secret`, `created_at`. Never put `secret` into `detail`. |
| `bank.beneficiaries` | `id`, `client_id`, `name`, `iban`, `added_by`, `approved_at` |
| `bank.bases` | `id`, `client_id`, `beneficiary_id`, `type` (`invoice`, `payroll`, `loan`), `amount_aed`, `approved`, `used` |
| `bank.payments` | `id`, `account_id`, `beneficiary_id`, `api_key_id`, `amount_aed`, `purpose`, `basis_id`, `created_at`. Executed payments only. |

## Time

All timestamps are local Gulf time (UTC+4, no daylight saving) stored as text `YYYY-MM-DD HH:MM:SS`, so SQLite `datetime()`, `time()` and `julianday()` work on them.
In the demo the request clock is simulated (header `X-Sim-Time`) and it goes backwards between scenarios.
So every time window needs both bounds: `created_at >= datetime(:now, '-5 minutes') AND created_at <= :now`.
"Prior payments" of a beneficiary are `bank.payments` rows with `created_at < :now`.
The webhook reports the window of the tripping rule; it reads the length from the first `datetime(:now, '-N minutes')` (or seconds, hours, days) in the rule, and uses 1 hour if there is none.

## The current payment is already inserted

When the rules run, the current payment is already in `reservations` with status `pending`, inside the same `BEGIN IMMEDIATE` transaction.
So a counter over `reservations` includes it: `COUNT(*) > 8` is violated by the 9th payment in the window.
`bank.payments` does not contain it yet.
When the key is paused, rules do not run at all: every payment is held with `rule_id = key_paused`.

## Soft and hard

- `soft`: the payment is held and layer 2 (the Jev decision model) may release it.
  If it is not released, the client is asked to confirm and the payment stays held.
- `hard`: the payment is held, the API key is paused (every later payment of the key is held), an incident is opened, its trace file is written and the investigator is notified.
  Jev is never asked.

When several rules are violated, the payment stores the first hard rule in file-name order, or the first soft rule if no hard rule fired.
A hold is reversible and a release is not, so when in doubt make the rule `hard`.

## Fail closed

A rule that raises an SQL error, returns no row or more than one row, or returns `violated` NULL counts as violated.
Its `detail` then starts with `rule error:`.
So a broken rule holds every payment: test it before you open a pull request.

## How to test a rule

1. Run the replay: `scripts/replay.sh` (or `scripts/replay.sh <incident_id>`).
   It seeds fresh databases in a temp dir, starts the bank and the breaker on free ports with `JEV_MODE=stub` and no webhook, replays `fixtures/legit.jsonl` first and the incident trace second, prints a table and exits 0 only if no attack payment executed and every legit payment executed.
   It needs no network and no secrets.
2. Run the tests: `pytest -q` (with the project's Python, for example `.venv/bin/python -m pytest -q`).
3. Add a regression test in a new file under `tests/` that replays the incident trace with `scripts/replaylib.py`:

```python
import replaylib  # tests/conftest.py puts scripts/ on sys.path


def test_inc_0001_is_fully_held(tmp_path):
    with replaylib.start(tmp_path) as stack:  # fresh seed, bank and breaker on free ports
        legit = replaylib.replay(stack.breaker_url, replaylib.LEGIT_TRACE)
        attack = replaylib.replay(stack.breaker_url,
                                  replaylib.INCIDENTS_DIR / "inc_0001.jsonl")
    assert attack.executed == 0
    assert legit.executed == legit.sent
```

`replaylib.replay()` returns a result with `sent`, `executed`, `held`, `rejected` and `lines` (one dict per request with its `status` and `reservation_id`).
`replaylib.start(tmp_path)` loads the live `rules/` folder, so the example uses your new rule.
The `stack` and `make_stack` fixtures of `tests/conftest.py` run on a pinned baseline of the six original rules instead, so do not use them for this test.
Inside the `with` block, `stack.get("/state")` returns the breaker state and `stack.env["BREAKER_DB"]` is the path of the temp breaker database, if you want to check which rule held a payment.
Always replay `fixtures/legit.jsonl` before an attack trace: once the attack pauses the key, every later payment of that key is held.

## Do not edit by hand

`rules/rules.lock` (checksums of the rule files) and `CHANGELOG.md` are generated files.
Never edit them by hand, and do not regenerate them in a pull request.
The maintainer runs `scripts/change_request.sh "<description>"` after the pull request is merged.
Until then `scripts/infra_check.sh` reports the new rule as a change without a change request; that is expected.
