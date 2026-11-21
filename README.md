# Night Circuit Breaker

A smart rate limiter in front of a toy bank payment API.
It counts money and client behaviour, not requests, and puts suspicious payments on a reversible hold.
`SPEC.md` is the build spec.

The bank is shown as ADCB and the client as Microsoft for illustration only.
The scenario is fictional.
All data is synthetic.
Neither company is involved.

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
.venv/bin/python scripts/traffic_soft.py              # 02:10 soft case, run before the attack, expect executed (Jev releases)
.venv/bin/python scripts/traffic_attack.py            # 02:00 attack, expect 6 executed, 36 held
scripts/apply_verdict.sh inc_0001 verdict.json        # send a verdict to the breaker
scripts/facts.sh held inc_0001                        # read-only facts for the investigator
scripts/infra_check.sh                                # was our own infrastructure changed?
scripts/replay.sh                                     # replay legit + latest incident on a fresh instance
scripts/change_request.sh "<description>"             # regenerate rules/rules.lock, add a CHANGELOG.md entry
.venv/bin/python scripts/jev_eval.py                  # live Jev check of 8 cases (needs TYPESAFE_API_KEY, uses ports 8300/8301)
scripts/watch_pr.sh                                   # wait for the agent's pull request, save it, apply its verdict
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

## Layer 2: Jev

A payment with only soft violations goes to Jev (`breaker/jev.py`) after its hold is committed.
`JEV_MODE=stub` (default) needs no network and is deterministic.
`live` calls `POST $JEV_URL` (default `https://api.typesafe.ai/v1/systemone`) with `Authorization: Bearer $TYPESAFE_API_KEY`, and may release.
`shadow` calls the API and logs the answer but never releases.
The breaker releases only if `fits_history >= JEV_T_HISTORY` and `purpose_matches_basis >= JEV_T_BASIS` (both default 0.9).
Timeout (`JEV_TIMEOUT_S`, 1 second total, no retry), connection error, non-200, malformed answer or a missing API key: the hold stays.
Every call is logged in `jev_calls` (request, response, latency, released, mode), and the API key is never stored.
Hard stops never call Jev.
Run `traffic_soft.py` after legit and before the attack.
After the attack the 1 hour sum rule makes the same payment a hard stop.
`traffic_soft.py --expect held` exits 0 when the payment stays held, for example with `JEV_MODE=live` and a dead `JEV_URL`.

The breaker keeps one `httpx.Client` for its whole life, so Jev calls reuse the TLS connection.
In `live` and `shadow` mode it opens that connection at start with `GET /v1/models` in a background thread; a failed warm-up only writes a `jev` event.

`scripts/jev_eval.py` checks the live decision on 8 cases and exits 0 only if all pass (2 if `TYPESAFE_API_KEY` is missing).
It seeds temp databases, drives the real bank and breaker in stub mode through legit, soft and attack traffic, and builds every case with `build_facts`.
Then it asks Jev `--n` times per case (default 5) and prints the scores (min, median, max), the decision, latency and PASS or FAIL.

| Case | Expected | What |
|---|---|---|
| R1 | release | the soft demo case: AED 48,000 to Falcon Cargo Handling, invoice `inv_4471` |
| H1, H2 | hold | attack payments 7 and 8, exactly as logged in `jev_calls` |
| H3 | hold | R1 without a basis |
| H4 | hold | R1 for AED 84,000 (basis says 48,000) |
| H5 | hold | R1 with purpose "Consulting retainer, Q4" |
| H6 | hold | attack payment 7 with a prompt injection as purpose |
| H7 | hold | first payment of AED 48,000 at 14:00 to a new supplier with a matching approved basis |

A release case passes when at least `n - n // 20` calls release it (19 of 20); a hold case passes only when every call holds.
`--model` compares models, `--cases R1,H5` picks cases, `--dump file.json` writes the facts and makes no API call.

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

## Incidents and verdicts

When an incident opens, the breaker POSTs a `breaker.tripped` event (SPEC section 9) to `DEVIN_WEBHOOK_URL`, with the header `X-Webhook-Secret: $DEVIN_WEBHOOK_SECRET`.
No webhook is sent when `DEVIN_WEBHOOK_URL` is unset or empty.
It is sent after the tripping transaction has committed, from a background thread, with a 3 second timeout.
Success or failure is logged as an `events` row; a failure never changes the hold, the pause or the HTTP response.
The webhook reports the Jev model from `JEV_MODEL` (default `jev-1.13.0`).

`POST /incidents/{id}/verdict` (header `X-Admin-Token`) takes the SPEC body:

```json
{"incident_id": "inc_0001", "verdict": "contact_client", "reason": "...",
 "evidence": {"basis_ids": [], "prior_payment_ids": []}, "pr_url": "https://..."}
```

`scripts/apply_verdict.sh <incident_id> <verdict.json>` sends it with `curl` to `BREAKER_URL` with `ADMIN_TOKEN`, prints the response and exits 1 on a non-2xx status.
Unknown incident: 404.
Bad body (unknown verdict, missing `reason`, unknown field, `incident_id` not equal to the path id): 422.
Closed incident: 409.

| Verdict | What the breaker does |
|---|---|
| `release` | Re-checks every held payment of the incident, one at a time: the beneficiary needs a bank payment before the hold, and an approved, unused basis for the same beneficiary with exactly the same amount (the payment's own `basis_id` if it has one). Passing payments go to the bank with that basis, so one basis releases at most one payment. If any payment fails the check, it stays held, the verdict is stored as `contact_client` and the key stays paused. If all pass, the key returns to `normal` and the incident is closed. |
| `hold_until_morning` | Stores `hold_until` = the next 08:00 (request clock) on the incident. Holds stay. |
| `contact_client` | Event "client contacted by second channel (simulated)". Holds stay. |
| `escalate_bank_security` | Event "bank security paged (simulated)". Key stays paused, holds stay. |

The `evidence` ids are stored but never trusted; the database decides.
`GET /state` returns `pause_to_pr_min` for each incident: real minutes from the pause to the verdict, null until a verdict with a `pr_url` arrives.

## Investigator scripts

The investigator (Devin) works on its own machine with a fresh clone and no access to the live databases or the breaker.
So every script runs on macOS (bash 3.2, BSD tools) and Linux, and does date math, checksums and JSON in Python.
The wrappers use `$PYTHON` if set, else `.venv/bin/python` if it exists, else `python3`, and stop with "run: pip install -r requirements.txt" if FastAPI is not importable.

| Script | What it does |
|---|---|
| `scripts/facts.sh <query> [args]` | Read-only queries: `key-history [api_key_id]`, `beneficiary <id>`, `bases <beneficiary_id>`, `held <incident_id>`. Uses `sqlite3 -readonly` when installed (3.33 or newer), else Python `sqlite3` with `mode=ro`; `FACTS_ENGINE=cli` or `python` forces one. Same output either way. |
| `scripts/infra_check.sh` | (a) commits of the last 24 hours in `rules/` (except `rules/rules.lock`), `breaker/`, `.env.example` and `requirements.txt`, each COVERED or NOT COVERED by `CHANGELOG.md`; (b) uncommitted changes there; (c) rule checksums against `rules/rules.lock`; (d) API keys created in the 24 hours before the latest incident; (e) breaker `/health` (not reachable is only a note). Exit 1 if (a) to (d) show a change without a change request. |
| `scripts/replay.sh [incident_id]` | Fresh seed in a temp dir, bank and breaker on free ports with `JEV_MODE=stub` and no webhook, replays `fixtures/legit.jsonl` and then the incident trace (default: the latest), prints the result table, exits 0 only if no attack payment executed and every legit payment executed. |
| `scripts/change_request.sh "<description>"` | The only writer of `CHANGELOG.md` and `rules/rules.lock`. Regenerates the lock and appends an entry with the short hashes of all commits in monitored paths not listed yet. Refuses to run with uncommitted changes there. |
| `scripts/replaylib.py` | The shared Python module behind these scripts and the tests: seed into a folder, start services on free ports, replay a trace, stop services by pid. See its docstring and `rules/README.md` for a regression test example. |

Databases for `facts.sh` and `infra_check.sh`: `BANK_DB` and `BREAKER_DB` if set and both exist, else `bank.db` and `breaker.db` in the repo root if both exist, else a snapshot.
The snapshot is built in `.state/` (gitignored): fresh seed, services in stub mode with no webhook, replay of `fixtures/legit.jsonl` and then the latest incident trace.
The seed is deterministic, so this reproduces the incident; one line on stderr says that a snapshot was built.
`docs/devin_prompt.md` is the prompt for the Devin automation, copied unchanged from SPEC section 10.
`rules/README.md` explains the rule format to an agent that adds a rule.

## Getting Devin's verdict back

Devin runs on its own machine and cannot reach the breaker, and it sends no callback.
So a watcher on the demo laptop looks for its pull request and feeds the verdict to the breaker:

```sh
scripts/watch_pr.sh                      # PRs opened from now on, GITHUB_REPO or the origin remote
scripts/watch_pr.sh --pr 3 --incident inc_0001 --interval 5
scripts/watch_pr.sh --no-apply           # save the artifacts only
```

It polls the GitHub API (`GITHUB_API_URL`, default `https://api.github.com`) every `--interval` seconds (default 10) until `--timeout-min` (default 30).
It takes the newest open pull request created at or after `--since` (default: the watcher start time) that has a valid `verdict.json`.
`--pr N` picks one pull request and ignores `--since`; `--incident ID` ignores verdict files for other incidents.
The token comes from `GITHUB_TOKEN`, else `gh auth token`; with neither, GitHub allows 60 requests per hour and the watcher warns once.
On a 403 or 429 it sleeps until the rate limit resets, at most 60 seconds, and tries again.

Artifacts go to `.state/pr/<number>/`: `verdict.json` as downloaded, `rules/` with every `rules/*.sql` at the pull request head, `report.md` if there is a `reports/*.md` file, and `pr.json`.
`.state/pr/latest.json` points to them for other scripts, paths relative to the repo root:

```json
{"number": 1, "url": "https://github.com/OWNER/REPO/pull/1", "head_sha": "...", "dir": ".state/pr/1",
 "verdict_file": ".state/pr/1/verdict.json", "rules_dir": ".state/pr/1/rules", "applied": true,
 "fetched_at": "2026-11-21T02:10:00Z"}
```

The verdict is sent to `BREAKER_URL` with `ADMIN_TOKEN`, like `scripts/apply_verdict.sh`.
`pr_url` is always set to the real pull request URL, and fields the endpoint does not accept are dropped with a note.
Everything from the pull request is untrusted: nothing is executed, only `rules/<name>.sql` with names matching `^[A-Za-z0-9_.-]+$` is saved, paths with `..` or a leading `/` are refused, and every download is capped at 200 KB.
Exit codes: 0 done, 2 setup error, 3 timeout, 4 the breaker refused the verdict or was unreachable (artifacts stay saved, `applied` is false), 5 GitHub API error.
`tests/test_watch_pr.py` runs the watcher against a fake GitHub API.

## Before the demo

Every commit of this project is younger than 24 hours, so `scripts/infra_check.sh` reports clean only after the change log covers them.
After the last code change is merged and committed:

```sh
scripts/change_request.sh "Demo build"
git add CHANGELOG.md rules/rules.lock
git commit -m "Record change request for the demo build"
scripts/infra_check.sh        # must print "infra check: clean"
```

The commit must touch only those two files.
Never edit `CHANGELOG.md` or `rules/rules.lock` by hand.
After merging a pull request that changes a rule, run the same steps again.

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

### Jev

- The SPEC's default `JEV_URL` was wrong.
  The default is the verified direct endpoint `https://api.typesafe.ai/v1/systemone`; `state` is sent as a JSON object, not a string.
- Variables: `JEV_MODE`, `JEV_URL`, `JEV_MODEL` (`jev-1.14.0`), `JEV_T_HISTORY`, `JEV_T_BASIS`, `JEV_TIMEOUT_S`, `TYPESAFE_API_KEY`.
  They are read once at breaker start.
- The default model is the pinned `jev-1.14.0`, not the `jev-latest` alias, so an alias move cannot change decisions.
  On the evaluation set both 1.13.0 and 1.14.0 pass, but 1.14.0 separates the cases more clearly (R1 0.98 and 0.99 against 0.94 and 0.98; H5 purpose 0.07 against 0.29).
- The 1 second timeout is a total: the client waits on a worker thread for at most `JEV_TIMEOUT_S`, and the HTTP call has the same limit per phase.
  One persistent `httpx.Client` (idle connections kept for 300 seconds instead of the httpx default of 5) and a shared pool of 4 worker threads serve all calls.
  A cold TLS connection costs about 450 ms against about 250 ms on a warm one, which is why the breaker warms the connection at start.
- Order: the hold is committed first, Jev runs outside any transaction, and a release goes `released` then `executed` with `decided_by = jev`.
  Any error inside the Jev step (including its logging) keeps the hold and writes an `error` event.
- Events: kind `jev`, text "Jev released ...", "Jev kept hold ..." or "Jev gave no answer ...".
  On a kept hold the existing "client asked to confirm payment" event is still written.
- `assess(facts, trace=None)` takes an optional dict that it fills with request, response, latency and mode, so the caller can write the `jev_calls` row.
- Facts are built by `build_facts(conn, res, now)` in `breaker/jev.py`.
  `basis.id` is the stored basis id (`inv_4471`), not the bare number.
  They are aggregates and record attributes only, never raw history and never a precomputed verdict.
  `basis` has `type`, `id`, `description`, `amount_aed`, `approved` and `already_used` (the bank's `used` flag).
  A basis that is missing or belongs to another beneficiary gives nulls and `approved: false`.
  `beneficiary` has `name`, `approved_days_ago`, `prior_payments`, `prior_payments_last_24h`, `min_amount_aed`, `median_amount_aed`, `avg_amount_aed`, `max_amount_aed`, `amount_within_prior_range`, `usual_hours`, `first_paid_days_ago`, `last_paid_days_ago` and `share_with_approved_basis` (share of prior payments whose basis is approved).
  `prior_payments_last_24h` is what tells an attack's "history" (2 payments minutes ago) from a real one.
  `usual_hours` is the earliest prior payment hour to the latest hour plus one (`09-18`).
  `payments_last_hour` counts this key's reservations in the last hour including this one.
  `share_of_balance_last_hour` is the sum reserved in that hour divided by the balance at the start of the hour, rounded to 3 digits.
  Beneficiary fields with no prior payments are null (`prior_payments` and `prior_payments_last_24h` are 0).
- `bank.bases` has a text column `description` (what was approved, such as "Invoice 4471 from Falcon Cargo Handling: logistics services, October 2026"), filled by the seed.
  Without it the model can only compare the purpose with an id and an amount: `purpose_matches_basis` for R1 rose from about 0.89 to 0.98 with it.
- An average interval between payments was tried and dropped: with the soft supplier's 31 day pause after a 4.6 day cadence it pulled `fits_history` for R1 down to about 0.88.
  `first_paid_days_ago`, `last_paid_days_ago` and `prior_payments` still carry the cadence.
- The two questions keep their ids and meaning and now spell out the yes/no boundary in `criteria`.
  `fits_history` says that time of day is not part of the question, because layer 1 already held the payment for it and the SPEC question is "by amount and frequency".
- Shadow mode logs `released = 0` in `jev_calls` even when the thresholds are met.
- `traffic_soft.py` prints `sent 1 executed N held M` and takes `--expect executed|held`.

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

### Incidents, verdicts and investigator scripts

- Held payments are tied to an incident by a new column `reservations.incident_id`.
  When an incident opens, the key's held payments of the last hour (the soft stops and the tripping payment) join it; every later hold of the key joins it while it is open.
- `incidents` has new columns: `tripped_wall_at` and `verdict_wall_at` (real time, UTC ISO 8601), `requested_verdict` (as sent), `reason`, `evidence_json`, `hold_until`.
  `verdict` is the verdict as executed, so a downgraded `release` shows `verdict = contact_client`, `requested_verdict = release`.
  A later verdict on the same open incident overwrites these fields; `pr_url` keeps its last non-empty value.
- Only `release` with every payment passing closes the incident.
  The other verdicts leave it open, so a second verdict can follow; admin resume still closes it.
- A verdict request without `X-Sim-Time` uses the request clock of the incident: the latest request time of its key.
  That time drives `hold_until`, `verdict_at`, the event times and the `X-Sim-Time` of payments a `release` sends to the bank.
- `release` re-check details: "prior payment" means a bank payment to the beneficiary before the hold was created, so in the demo the two leaked phase A payments count for each dormant beneficiary, and only the missing basis keeps them held.
  A basis carried by another payment in flight in the breaker (`pending` or `released`) is not available.
  Verdicts run one at a time (a lock in the breaker process), and each payment moves from `held` to `released` in its own `BEGIN IMMEDIATE` transaction before it is sent.
  A payment that the bank rejects or cannot be reached for counts as not passed.
- The verdict body is strict: unknown fields are rejected, `reason` is 1 to 2000 characters, `pr_url` is optional and must start with `http://` or `https://`.
- Webhook: the window length is read from the tripping rule's first `datetime(:now, '-N unit')` (1 hour if none); `count` and `sum_aed` count the same statuses as the rules (`pending`, `executed`, `held`, `released`).
  `level` is the level stored on the tripping payment.
  `sample_requests` are the first 20 of the key's requests in the hour up to `tripped_at`, in trace format, taken from the database.
  `trace_file` is relative to the repo root when the file is inside the repo, else the configured path.
  `X-Webhook-Secret` is only sent when `DEVIN_WEBHOOK_SECRET` is set.
  Webhook outcomes are `events` rows of kind `webhook` (sent) or `error` (failed), timed at the incident's `tripped_at`.
- `facts.sh`: "now" is the request clock, the latest request seen by the breaker (real Gulf time if there is none); `key-history` without an argument uses the key of the latest incident.
  With the CLI, arguments are bound with `.parameter set` after the `^[A-Za-z0-9_]+$` check; Python binds them as parameters.
- The snapshot replays the incident trace with the rule files of the commit that last changed that trace (`git show`), so it still shows the incident as it happened after a new rule is added.
  It falls back to the working tree rules without git, warns on stderr if replayed outcomes differ from the trace, and is reused until the latest incident trace changes.
- `infra_check.sh`: commit times are committer dates; a commit is COVERED when a hex token of 7 or more characters in `CHANGELOG.md` is a prefix of its hash; a key is "named" when its id appears anywhere in `CHANGELOG.md`.
  Untracked files in monitored paths count as uncommitted changes.
  `rules/rules.lock` lists `sha256  file name` for `rules/*.sql` only.
- `replay.sh` exits 2 on a usage error (unknown incident id), 1 when a criterion fails.
  It stops its services through a pid file and a shell `trap`, and Python stops them too.

### Getting Devin's verdict back

- The watcher uses the PR's `html_url` from GitHub as `pr_url`, whatever `verdict.json` says.
- Verdict file lookup: among the changed files, the `verdict.json` nearest to the repo root; paths with `..`, a leading `/` or a backslash are refused.
- A verdict file that is missing, not valid JSON, not an object or larger than 200 KB counts as "not there yet": the watcher keeps polling, because the agent may push more commits.
- A rule file that is too large or has an unsafe name is skipped with a warning and the rest are saved; `.state/pr/<number>/` is rebuilt from scratch on each run.
- `latest.json` is written once with `applied: false` after the artifacts are saved, and again with `applied: true` after the breaker accepted the verdict.
- Any breaker failure (HTTP error or not reachable) exits 4.
- `--timeout-min 0` means one poll, then exit 3 if nothing usable was found.
- The test-only option `--repo-root` moves `.state/` away from the repo.
