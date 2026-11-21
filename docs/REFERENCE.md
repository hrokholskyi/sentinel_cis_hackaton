# TrustGap reference

The product overview and the quick start are in the [README](../README.md).

TrustGap is a payment circuit breaker: a smart rate limiter in front of a toy bank payment API.
It counts money and client behaviour, not requests, and puts suspicious payments on a reversible hold.
Three layers decide: SQL rules (milliseconds), the Jev decision model (under a second) and Devin, a coding agent (minutes).

Three external tools do real work here.
Jev, a decision model from TypeSafe, is layer 2: it may release a soft stop.
Devin, a coding agent, is layer 3: it investigates an incident, writes a verdict and opens a pull request with a new rule.
Graphify (the open source PyPI package `graphifyy`, version 0.9.114) built the code knowledge graph in `graphify-out/` with its local code-only extraction, with no model calls.
The Devin prompt (`docs/devin_prompt.md`, step 5) tells Devin to use that graph to list every code path that reaches the payout function, so a missing check is enforced at one choke point.

`SPEC.md` is the build spec.
It calls the product by its working name, Night Circuit Breaker.
`docs/demo_preview.html` is a simulated walk-through of the demo that needs no services.

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

## Run the demo on stage

One command does everything: seed, services, browser, guided demo.

```sh
scripts/demo.sh                 # manual: press Enter for each beat
scripts/demo.sh --auto          # advance on timers
scripts/demo.sh --from 3        # start at case 3 on a fresh seed (case 3 does not need cases 1 and 2)
scripts/demo.sh --keep-data --from 4   # continue after a crash, on the same databases
scripts/demo.sh --no-tour --no-open
```

What `scripts/demo.sh` does, in order:

1. Loads `.env` if present, only for the variables that the environment does not set already.
   So `JEV_MODE=stub scripts/demo.sh` runs in stub mode even when `.env` sets `JEV_MODE=live`, and the same holds for the URLs.
2. Picks `JEV_MODE=live` when `TYPESAFE_API_KEY` is set and `JEV_MODE` is not, else `stub`.
3. Reseeds `BANK_DB` and `BREAKER_DB`.
   With `--keep-data` it skips the seed and keeps both databases; it refuses if one of them does not exist.
4. Starts the bank and the breaker on the ports of `BANK_URL` and `BREAKER_URL` (8001 and 8000 by default).
   It refuses to start if a port is busy.
   The breaker writes incident traces to `.state/demo/incidents/`, so a demo never changes the committed `fixtures/incidents/`.
5. Waits for both health checks.
6. Opens the dashboard in the background (`open -g` on macOS, `xdg-open` on Linux, skipped with `--no-open`) and prints a reminder to keep the terminal focused, because Enter must reach the runner.
7. Runs `scripts/demo.py` with the other arguments.

On exit it stops exactly the two services it started, by pid.
Service logs go to `.state/demo/`.

After the last beat the runner leaves the page as it is and says that the demo is finished.
The services keep running until `q` then Enter, or Ctrl-C.
Only then does the runner clear the presenter state, and `demo.sh` stop the services.

When the runner stops on an error or on Ctrl-C, it prints the command that continues at the current case on the same databases, for example `scripts/demo.sh --keep-data --from 4`.
It adds `--no-open`, `--no-webhook` and `--no-watcher` when the run had them.

## Rehearse without external calls

```sh
JEV_MODE=stub scripts/demo.sh --no-webhook --no-watcher
```

- `--no-webhook` (an option of `demo.sh`) unsets `DEVIN_WEBHOOK_URL` and `DEVIN_WEBHOOK_SECRET`, so no Devin session starts, even when `.env` sets them.
- `--no-watcher` (an option of `demo.py`) never starts the PR watcher, so nothing polls GitHub.
- `JEV_MODE=stub` keeps Jev offline.
  A value in the environment wins over `.env`, so this works even if `.env` sets `JEV_MODE`.

Use other ports when the default ones are busy, for example `BANK_URL=http://127.0.0.1:8501 BREAKER_URL=http://127.0.0.1:8500`.

## Demo order and keys

`scripts/demo.py` drives the page through the presenter endpoints and runs the traffic scripts as subprocesses.

1. Tour: 7 stops with a spotlight on each part of the page.
2. Case 1, legit traffic: 42 daytime payments with a 0.12 s delay, all executed.
3. Case 2, soft stop: the 02:10 payment.
   Layer 1 holds it and Jev releases it.
   If Jev does not answer in time, or answers with an error, the payment stays on hold and the caption says which one.
   A failed call that took the whole `JEV_TIMEOUT_S` counts as a timeout, a faster one (for example HTTP 401) as an error.
4. Case 3, attack: the 02:00 attack with a 0.25 s delay.
   Captions follow the real state: leak, first soft stop, API key paused, every further payment held and the webhook status, then the totals.
5. Case 4, Devin: the webhook status (sent, failed, or not configured), then, only when it was sent, "Devin is working" with a timer, until the incident has a verdict and a pull request.
   The verdict caption says what the breaker did with it, including a release that the re-check changed to `contact_client`.
6. Case 5, replay: `scripts/replay.sh` with the repo rules (6 executed, FAIL), then with `--rules-dir` from the pull request whose verdict the breaker holds (0 executed, PASS).

Each case has an EXPLAIN beat (title card and captions) and a SHOW beat.
Keys in the terminal:

| Key | When | What it does |
|---|---|---|
| Enter | between beats | next beat |
| `f` then Enter | case 4, while waiting | apply the verdict of the rehearsal run named in `.state/pr/rehearsal.json` (else `.state/pr/latest.json`), with a caption that says so |
| `s` then Enter | case 4, while waiting | skip the wait; the caption follows the webhook status |
| `q` then Enter | after the last beat | end the demo: clear the presenter state, stop the services |
| Ctrl-C | any time | clear the presenter state, stop the child processes, stop the services, print the restart command |
| `C` | on the dashboard | subtitles on or off |

Each prompt starts with the time since the demo started, as `[mm:ss]`.
The runner discards keys pressed while a step runs, so an Enter pressed too early never skips the next pause.

When the incident opens, the runner starts `scripts/watch_pr.py --since <tripped_wall_at> --incident <id> --timeout-min 30` if that script exists.
Its log is `.state/demo/watch_pr.log`.
When the watcher exits, the terminal shows its exit code and what it means, for example "code 2: setup or authentication error, for example a bad GitHub token".

The fallback sends the verdict with the header `X-Verdict-Source: rehearsal`.
The breaker stores that source, so the incident card and Layer 3 show "from the rehearsal run" instead of minutes from today's pause, and the caption says that this is the pull request from the rehearsal run.

Case 5 replays against the `rules_dir` of the pull request whose verdict the breaker holds: the one of `rehearsal.json` and `latest.json` whose `url` is the incident's `pr_url`.
A rehearsal pull request is labelled "(rehearsal run)".
Without a match the runner shows only the "before" table and says why.

Options of `demo.py`: `--auto`, `--from N`, `--no-tour`, `--pace` (multiplies caption times), `--legit-delay`, `--attack-delay`, `--no-hold`, `--no-watcher`, `--watcher PATH`, `--pr-state PATH`, `--rehearsal-state PATH` (default: `rehearsal.json` next to `--pr-state`), `--presenter-log PATH` (every presenter update as a JSON line, for tests), `--before-rules DIR`, `--url`.
`--before-rules DIR` sets the rules folder of the "before" replay in case 5.
The default is the repo `rules/` folder, which is right on stage; tests pass the baseline folder.

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

After a successful rehearsal, keep its pull request as the stage fallback (replace `N` with its number):

```sh
scripts/watch_pr.sh --pr N --no-apply --save-as rehearsal
```

This writes `.state/pr/rehearsal.json`.
A later live run writes only `latest.json`, so the rehearsal pointer stays.

## Commands

```sh
.venv/bin/python scripts/seed.py                      # create bank.db and breaker.db from scratch
.venv/bin/uvicorn bank.app:app --port 8001            # toy bank
.venv/bin/uvicorn breaker.app:app --port 8000         # breaker, clients only talk to this
.venv/bin/python scripts/traffic_legit.py             # 42 daytime payments, expect 42 executed
.venv/bin/python scripts/traffic_soft.py              # 02:10 soft case, run before the attack, expect executed (Jev releases)
.venv/bin/python scripts/traffic_attack.py            # 02:00 attack, expect 6 executed, 36 held
.venv/bin/python scripts/traffic_payroll.py           # payroll on the 25th at 09:00, fresh seed, expect 8 executed, 22 held
scripts/apply_verdict.sh inc_0001 fixtures/verdicts/payroll_release.json   # release the payroll burst, expect 30 of 30 executed
scripts/apply_verdict.sh inc_0001 verdict.json        # send a verdict to the breaker
scripts/facts.sh held inc_0001                        # read-only facts for the investigator
scripts/infra_check.sh                                # was our own infrastructure changed?
scripts/replay.sh                                     # replay legit + latest incident on a fresh instance
scripts/replay.sh --rules-dir <dir>                   # same replay with the rules of another folder (a PR checkout)
scripts/change_request.sh "<description>"             # regenerate rules/rules.lock, add a CHANGELOG.md entry
.venv/bin/python scripts/jev_eval.py                  # live Jev check of 8 cases (needs TYPESAFE_API_KEY, uses ports 8300/8301)
scripts/watch_pr.sh                                   # wait for the agent's pull request, save it, apply its verdict
open http://127.0.0.1:8000/                           # live dashboard, served by the breaker
.venv/bin/python scripts/demo.py                      # guided demo against running services (presenter mode)
scripts/demo.sh                                       # the one command for the stage: seed, services, browser, demo
GRAPHIFY_BACKEND=none graphify extract . --code-only && GRAPHIFY_BACKEND=none graphify cluster-only .   # refresh graphify-out/ (uv tool install "graphifyy[sql]==0.9.114")
```

`traffic_legit.py` rewrites `fixtures/legit.jsonl`.
`traffic_attack.py` rewrites `traces/attack.jsonl` (gitignored).
On the attack the breaker writes `fixtures/incidents/inc_0001.jsonl`.
Run the attack on a fresh seed (or after legit).
A second attack run on the same databases only meets a paused API key.

Each traffic script prints a one-line summary such as `sent 42 executed 42 held 0` and exits non-zero when the result differs from SPEC section 8.
Use `--delay <seconds>` to change the real pause between requests and `--trace <path>` to write the JSONL trace somewhere else.

Tests start both services on free ports against temp databases:

```sh
.venv/bin/python -m pytest -q
```

## Breaker endpoints

Besides `POST /payouts`:

| Endpoint | What it does |
|---|---|
| `GET /health` | Health check on both services; the breaker also lists the loaded rule ids |
| `GET /state` | API key states, reservation counts and sums by status, open incident, all incidents, last 10 events |
| `POST /admin/keys/{id}/resume` | API key back to `normal`, closes its open incident |
| `POST /admin/holds/{id}/release` | Sends a held payment to the bank (marked `released`, then `executed`) |
| `POST /admin/holds/{id}/reject` | Marks a held payment `rejected`; it is never sent |
| `GET /` | The live dashboard (`breaker/static/index.html`) |
| `POST /admin/presenter` | Sets presenter fields (subtitle, spotlight, title card, stepper, panel); kept in memory |
| `DELETE /admin/presenter` | Clears the presenter state; the page becomes a plain live dashboard |

Admin endpoints need the header `X-Admin-Token: $ADMIN_TOKEN`.

## Layer 2: Jev

A payment with only soft violations goes to Jev (`breaker/jev.py`) after its hold is committed.
Hard stops never call Jev.

| `JEV_MODE` | What it does |
|---|---|
| `stub` (default) | No network, deterministic. |
| `live` | Calls `POST $JEV_URL` (default `https://api.typesafe.ai/v1/systemone`) with `Authorization: Bearer $TYPESAFE_API_KEY`, and may release. |
| `shadow` | Calls the API and logs the answer, but never releases. |

The breaker releases only if `fits_history >= JEV_T_HISTORY` and `purpose_matches_basis >= JEV_T_BASIS` (both default 0.9).
The hold stays on a timeout (`JEV_TIMEOUT_S`, 1 second total, no retry), a connection error, a non-200 status, a malformed answer or a missing API key.
Every call is logged in `jev_calls` (request, response, latency, released, mode), and the API key is never stored.

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

A release case passes when at least `n - n // 20` calls release it (19 of 20).
A hold case passes only when every call holds.
`--model` compares models, `--cases R1,H5` picks cases, `--dump file.json` writes the facts and makes no API call.

## Dashboard

`GET /` on the breaker serves one static file, `breaker/static/index.html`: vanilla JS, no build step, no CDN, no web fonts, works offline.
It polls `GET /state` every 500 ms and only renders; every number is computed by the breaker in `breaker/dashboard.py`.
It is laid out for a stage screen and holds at 1920x1080 and 1440x900 with no page scrollbars.

What it shows, all from the databases:

- API key state badge (NORMAL, PAUSED, PROBE) for the client's API key, with the client name from `bank.clients`.
  The flip to PAUSED flashes the screen and shows "KEY PAUSED" for a moment.
- Scenario clock: the request time of the latest reservation.
- Counters: stopped, leaked, legitimate payments held, released by Jev with median latency, first violation to pause.
- The three layers, each with its latest real decision: the last rule result, the last Jev call (both scores, thresholds, latency, mode; "no answer, hold kept" when Jev did not answer), and the incident with webhook status, verdict and pull request.
- Payment stream (latest 60 reservations, newest on top), hold queue (last 15 held), incident card, last 10 events.

`GET /state` keeps every earlier field and adds `bank_name`, `client`, `key`, `clock`, `jev_mode`, `counters`, `stream`, `hold_queue`, `incident`, `layers` and `presenter`.
Counter definitions:

| Counter | Definition |
|---|---|
| Stopped | Reservations with status `held`: count and AED sum |
| Leaked | For each incident, the API key's `executed` reservations created in the hour up to `tripped_at`, except those sent later by a verdict or an admin |
| Legitimate payments held | Held reservations that would pass the release re-check of a verdict (`incidents.recheck`) |
| Released by Jev | `jev_calls` rows with `released = 1`; median latency over the calls that got a valid answer |
| First violation to pause | `violation_to_pause_ms` of the open incident, else the latest one |

Presenter mode: `POST /admin/presenter` (header `X-Admin-Token`) takes any of `caption`, `spotlight`, `card`, `step`, `step_done`, `panel`.
A missing field keeps its value and `null` clears it; `DELETE /admin/presenter` clears everything.

- `spotlight` is one of `all`, `layers`, `layer1`, `layer2`, `layer3`, `key`, `counters`, `stream`, `queue`, `incident`, `events`.
- `card` is `{"kicker": "Case 2", "title": "Soft stop"}`.
- `step` is 0 (tour) to 5, and `step_done` is a list of steps.
- `panel` is `{"title", "text"}`, shown in monospace over the stream and hold queue.

The state lives in the breaker process, so a restart clears it.
With no presenter state the page is a plain live dashboard: no stepper, no subtitles, no dimming.
On the page, the key `C` shows or hides the subtitles.

## Milestone 6: probe, expiry, payroll

### Payroll scenario

```sh
.venv/bin/python scripts/seed.py
INCIDENTS_DIR=traces/incidents .venv/bin/uvicorn breaker.app:app --port 8000   # keeps fixtures/incidents/ clean
.venv/bin/python scripts/traffic_payroll.py
scripts/apply_verdict.sh inc_0001 fixtures/verdicts/payroll_release.json
```

`traffic_payroll.py` sends 30 salary payments to `ben_emp_01` .. `ben_emp_30` on 2026-11-25 (the 25th of `DEMO_DATE`'s month) from 09:00:00 to 09:00:58, one every 2 seconds.
Each one carries the employee's approved payroll basis for this month (`pr_2026_11_ben_emp_NN`) with the exact salary.
The salaries are the client's payroll file: the script reads them from those bases in `BANK_DB` (read-only, `--bank-db` overrides).
Flags as in the other traffic scripts: `--delay` (default 0.1), `--trace` (default `traces/payroll.jsonl`), `--url`.

Expected outcome, checked by the script (exit 1 otherwise):

| Payments | Time | Outcome |
|---|---|---|
| 1-8 (`ben_emp_01` .. `ben_emp_08`) | 09:00:00 to 09:00:14 | executed, no rule fires |
| 9 (`ben_emp_09`) | 09:00:16 | held, `hard_stop`, `burst_count_5m` (9 reservations in 5 minutes), key paused, `inc_0001` opens |
| 10-30 | 09:00:18 to 09:00:58 | held, `key_paused` |

So 8 executed, 22 held.
No other rule fires: 09:00 is business hours, each salary equals the prior salaries, the first 8 sum to AED 84,300.
The script prints the open incident id and the `apply_verdict.sh` command.
`fixtures/verdicts/payroll_release.json` is the example verdict: `release` for `inc_0001`, with the 22 held basis ids as evidence.
The breaker re-checks each held payment (salary history, approved unused basis of the same amount) and sends all 22: 30 of 30 executed, key `normal`, incident `closed`.

### Hold expiry

A hold has a time limit, `HOLD_TTL_MIN` (default 30).
Expiry is lazy and runs on the request clock only: when a payout for the key arrives, and when a verdict or an admin call for that key, incident or hold is processed.
Never on a timer, never on wall-clock time, never on `GET /state`.

- The holds of an open incident share one deadline: the latest of them plus `HOLD_TTL_MIN`, or the incident's `hold_until` from a `hold_until_morning` verdict if that is later.
  A request with a request time strictly later than the deadline rejects all of them (`status = rejected`, `decided_by = expiry`), with one `expiry` event per payment and one for the incident.
  They are never sent.
  The incident gets `status = expired` and a paused key moves to `probe`.
- Any other hold (a soft stop with no incident, a probe hold) expires on its own, `HOLD_TTL_MIN` after it was created, and the key state does not change.
- A verdict for an expired incident answers 409 "incident is expired".
  An admin release or reject of an expired hold answers 409 "reservation is rejected, not held".
- Admin calls and verdicts without `X-Sim-Time` use the key's latest request time as "now", so they never expire anything a request would not have expired.

In the main demo nothing expires: the attack holds are from 02:03:00 to 02:04:10, the latest request is the soft payment at 02:10, and verdicts without `X-Sim-Time` run at 02:10.
After `hold_until_morning` they last until 08:00.
Without it, the first payout of the key after 02:34:10 expires the 36 holds and the key moves to `probe`.

### Probe

A key in `probe` evaluates the rules as usual, then the probe gate: the beneficiary needs a bank payment before now and the amount must be at most `PROBE_MAX_AMOUNT_AED` (default 1000).
A payment that fails the gate is held with `reason = probe` in the 202 response, `rule_id = probe`, `level = hard`, and the reason in `detail`.
A hard rule violation pauses the key again and opens a new incident (`inc_0002`, ...), before the gate.
Soft violations that pass the gate take the normal Jev path.
A payment with no violation that the bank executes is clean (`decided_by = probe`); after `PROBE_CLEAN_PAYMENTS` (default 5) clean payments the key returns to `normal`.
The count is `key_state.probe_clean` in `breaker.db`, so it survives a breaker restart; every state change resets it.

### What `GET /state` shows

- `key.state` is `normal`, `paused` or `probe`; `key` also has `probe_clean` and `probe_target`.
  In `probe`, `key.incident_id` is the expired incident.
- `counters.stopped` counts `held` only, so an expired payment is not stopped any more.
- `counters.expired` is new: holds rejected by expiry, count and AED sum.
- `counters.leaked` no longer counts the payments before an incident that a full `release` verdict closed: that traffic was legitimate (the payroll burst).
- `incident.expired_count` is the number of the incident's holds rejected by expiry.
- `layers.layer1.result` is `probe` when the latest reservation was held by the probe gate.
- Holds past their deadline stay `held` in `/state` until the next request for the key arrives.

## Environment variables

See `.env.example` for all variables and their defaults.
The services do not read `.env` themselves; load it with `set -a; . ./.env; set +a` if you need other values.
`scripts/demo.sh` loads it for you.

| Variable | Default | Used by |
|---|---|---|
| `BANK_DB` | `bank.db` | bank, breaker (read-only attach), seed |
| `BREAKER_DB` | `breaker.db` | breaker, seed |
| `BANK_URL` | `http://127.0.0.1:8001` | breaker |
| `BREAKER_URL` | `http://127.0.0.1:8000` | traffic scripts |
| `RULES_DIR` | `rules` | breaker, loaded once at start |
| `INCIDENTS_DIR` | `fixtures/incidents` | breaker, incident trace files (`scripts/demo.sh`: `.state/demo/incidents`) |
| `TRACE_FILE_PUBLIC_DIR` | not set | breaker: folder named in the webhook's `trace_file` (`scripts/demo.sh`: `fixtures/incidents`) |
| `DEMO_MODE` | `1` | bank, breaker: take "now" from the `X-Sim-Time` header |
| `ADMIN_TOKEN` | `dev-admin-token` | breaker admin endpoints |
| `SOFT_MIN_AMOUNT_AED` | `5000` | rules, bound as `:soft_min_amount_aed` |
| `HOLD_TTL_MIN` | `30` | not used before milestone 6 |
| `DEMO_DATE` | `2026-11-21` | seed, traffic scripts |
| `BANK_NAME` | `ADCB` | dashboard header and demo captions (text only) |
| `SIM_TOKEN` | `dev-sim-token` | bank, breaker: `X-Sim-Time` counts only with this value in `X-Sim-Token`; traffic scripts and replay send it |
| `ALLOWED_HOSTS` | `127.0.0.1,localhost` | bank, breaker: accepted `Host` header values, comma separated |

## Incidents and verdicts

When an incident opens, the breaker POSTs a `breaker.tripped` event (SPEC section 9) to `DEVIN_WEBHOOK_URL`, with the header `X-Webhook-Secret: $DEVIN_WEBHOOK_SECRET`.
No webhook is sent when `DEVIN_WEBHOOK_URL` is unset or empty.
It is sent after the tripping transaction has committed, from a background thread, with a 3 second timeout.
Success or failure is logged as an `events` row; a failure never changes the hold, the pause or the HTTP response.
The webhook reports the Jev model that the breaker uses: `JEV_MODEL` in `breaker/jev.py` (default `jev-1.14.0`).
Its `trace_file` is the path of the trace relative to the repo, or `TRACE_FILE_PUBLIC_DIR/<id>.jsonl` when that variable is set.
`scripts/demo.sh` writes the traces to `.state/demo/incidents/` and sets `TRACE_FILE_PUBLIC_DIR=fixtures/incidents`, because the external agent reads the committed trace in its own clone.

`POST /incidents/{id}/verdict` (header `X-Admin-Token`) takes the SPEC body:

```json
{"incident_id": "inc_0001", "verdict": "contact_client", "reason": "...",
 "evidence": {"basis_ids": [], "prior_payment_ids": []}, "pr_url": "https://..."}
```

`scripts/apply_verdict.sh <incident_id> <verdict.json>` sends it with `curl` to `BREAKER_URL` with `ADMIN_TOKEN`, prints the response and exits 1 on a non-2xx status.

| Response | When |
|---|---|
| 404 | Unknown incident |
| 422 | Bad body: unknown verdict, missing `reason`, unknown field, or `incident_id` not equal to the path id |
| 409 | Closed incident |

| Verdict | What the breaker does |
|---|---|
| `release` | Re-checks every held payment of the incident, one at a time: the beneficiary needs a bank payment before the hold, and an approved, unused basis for the same beneficiary with exactly the same amount (the payment's own `basis_id` if it has one). Passing payments go to the bank with that basis, so one basis releases at most one payment. If any payment fails the check, it stays held, the verdict is stored as `contact_client` and the API key stays paused. If all pass, the API key returns to `normal` and the incident is closed. |
| `hold_until_morning` | Stores `hold_until` = the next 08:00 (request clock) on the incident. Holds stay. |
| `contact_client` | Event "client contacted by second channel (simulated)". Holds stay. |
| `escalate_bank_security` | Event "bank security paged (simulated)". API key stays paused, holds stay. |

The `evidence` ids are stored but never trusted; the database decides.
The optional header `X-Verdict-Source: rehearsal` (or `live`, the default) marks a verdict that comes from an earlier rehearsal run; any other value is a 422.
`scripts/apply_verdict.sh` sends it when `VERDICT_SOURCE=rehearsal` is set.
`GET /state` returns `pause_to_pr_min` for each incident: real minutes from the pause to the verdict, null until a verdict with a `pr_url` arrives, and null for a rehearsal verdict.
It also returns `verdict_source` (`live` or `rehearsal`, null before a verdict).

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

Devin runs on its own machine, cannot reach the breaker, and sends no callback.
So a watcher on the demo laptop looks for its pull request and feeds the verdict to the breaker:

```sh
scripts/watch_pr.sh                      # PRs opened from now on, GITHUB_REPO or the origin remote
scripts/watch_pr.sh --pr 3 --incident inc_0001 --interval 5
scripts/watch_pr.sh --no-apply           # save the artifacts only
```

It polls the GitHub API (`GITHUB_API_URL`, default `https://api.github.com`) every `--interval` seconds (default 10) until `--timeout-min` (default 30).
It takes the newest open pull request created at or after `--since` (default: the watcher start time) that has a valid `verdict.json`.
`--pr N` picks one pull request and ignores `--since`; `--incident ID` ignores verdict files for other incidents.
Only pull requests whose head branch lives in the watched repository are used, so a stranger cannot feed the breaker from a fork.
A pull request from a fork is skipped with one log line that names its number and author; `--allow-forks` switches that check off.
`--authors a,b` (or `WATCH_PR_AUTHORS`) limits the accepted authors, case-insensitive; without it any author with a branch in the repository is accepted, and the "found PR" line names the author.
With `--pr N` the same checks apply, and a refusal exits 2.
The token comes from `GITHUB_TOKEN`, else `gh auth token`; with neither, GitHub allows 60 requests per hour and the watcher warns once.
On a 429, or a 403 with `Retry-After` or `X-RateLimit-Remaining: 0`, it sleeps until the rate limit resets, at most 60 seconds, and tries again.
A 401, or a 403 without that rate-limit information, is an authentication error: it says to check `GITHUB_TOKEN` and exits 2.
Connection errors, timeouts and HTTP 5xx are retried with a backoff of 0.5, 1, 2, 4 and then 5 seconds until the deadline, then it exits 5.
`--save-as rehearsal` writes the pointer to `.state/pr/rehearsal.json` instead of `latest.json` (see "Before the demo").

Artifacts go to `.state/pr/<number>/`: `verdict.json` as downloaded, `rules/` with every `rules/*.sql` at the pull request head, `report.md` if there is a `reports/*.md` file, and `pr.json` (with the author login and `head_repo`).
`.state/pr/latest.json` points to them for other scripts, with paths relative to the repo root:

```json
{"number": 1, "url": "https://github.com/OWNER/REPO/pull/1", "head_sha": "...", "dir": ".state/pr/1",
 "verdict_file": ".state/pr/1/verdict.json", "rules_dir": ".state/pr/1/rules", "applied": true,
 "fetched_at": "2026-11-21T02:10:00Z"}
```

The verdict is sent to `BREAKER_URL` with `ADMIN_TOKEN`, like `scripts/apply_verdict.sh`.
`pr_url` is always set to the real pull request URL, and fields the endpoint does not accept are dropped with a note.
Everything from the pull request is untrusted: nothing is executed, only `rules/<name>.sql` with names matching `^[A-Za-z0-9_.-]+$` is saved, paths with `..` or a leading `/` are refused, and every download is capped at 200 KB.
Malformed API answers (a number that is not a positive integer, a bad head sha, a URL that is not https) are skipped with one log line.
Exit codes: 0 done, 2 setup or authentication error or a refused `--pr`, 3 timeout, 4 the breaker refused the verdict or was unreachable (artifacts stay saved, `applied` is false), 5 GitHub API error (still failing at the deadline).
`tests/test_watch_pr.py` runs the watcher against a fake GitHub API.

## Security hardening

Fixes from the code and security review.
Each one has a test in `tests/test_hardening_*.py` that reproduces the problem on a real stack.

- Jev release is re-checked: after Jev answers `release`, one `BEGIN IMMEDIATE` transaction requires that the reservation is still the soft hold made by the rules (not decided by an admin, a verdict or expiry, not attached to an incident) and that the key is still `normal`.
  Only then does it become `released`, and only after the commit is it sent; otherwise the hold stays and a `jev` event says why.
- One basis releases one payment: a Jev release needs the payment's own `basis_id`, approved, unused, of this client and beneficiary, with exactly the payment amount, and not carried by another `pending`, `released` or `executed` reservation.
  This is the same re-check as a `release` verdict (`incidents.recheck`), so a prompt injection in `purpose` cannot release a payment without such a basis.
  Jev is still asked and its answer is logged; this code gate decides whether a `release` answer may be acted on, and the stub scores a used basis low.
- A rule error blocks a Jev release: if any violation comes from a rule error, the payment stays on hold.
- The simulated clock needs a token: `X-Sim-Time` counts only with `X-Sim-Token` equal to `SIM_TOKEN` (constant-time compare) and only with `DEMO_MODE=1`.
  Without a valid token the header is ignored, real time is used and the breaker writes a `clock` event; the breaker passes the token to the bank only with a time it accepted.
- Rules are read-only: they run under a SQLite authorizer that allows only reads, so a statement that writes is a rule error (violated).
  A rule file that is not a single `SELECT` or `WITH ... SELECT` stops the breaker from starting.
- No double payment on an unclear bank outcome: the breaker sends the reservation id as `Idempotency-Key`, and the bank answers a repeated key with the stored payment.
  Before any send the breaker looks for a bank payment with that key and, if there is one, marks the reservation `executed` instead of sending.
- Restart: at start the breaker settles every `pending` or `released` row without a payment id: `executed` if the bank has its payment, otherwise `held` with rule id `restart_unclear` and an event.
- A verdict updates only an incident that is still open, so it cannot reopen one that an admin resume closed while a release ran.
- Both services accept only the `Host` values in `ALLOWED_HOSTS` (default `127.0.0.1,localhost`), against DNS rebinding from a page in the stage browser.
- `scripts/demo.sh` generates a random `ADMIN_TOKEN` and `SIM_TOKEN` for each run when they are not set, passes them to everything it starts and writes them to `.state/demo/tokens.env` (mode 600, never printed).
  In another terminal load them with `set -a; . .state/demo/tokens.env; set +a` before `scripts/apply_verdict.sh` or `scripts/watch_pr.sh`.
- `scripts/demo.py` starts the PR watcher with `--incident <id>` of the open incident, so a verdict file for another incident is ignored.

### Known limits

- The bank accepts the client's API key directly, so a client that calls the bank itself bypasses the breaker.
  The toy bank has this gap by design (SPEC section 5).
- The development defaults `dev-admin-token` and `dev-sim-token` are public.
  Outside `scripts/demo.sh`, set `ADMIN_TOKEN` and `SIM_TOKEN` yourself when anyone else can reach the services.
- Every request with an ignored `X-Sim-Time` writes one event, so such a client can push other events out of the dashboard's last 10.
- The bank does not compare the body of a repeated `Idempotency-Key` with the stored payment; it returns the stored payment.
- The idempotency key is the reservation id, which is unique only while `bank.db` and `breaker.db` are seeded together (the seed always recreates both).
- A soft stop on a key in `probe` is never released by Jev, because the key must be `normal`.

## Choices left open by the spec

### Calendar, time and seed

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

### Tests

- Tests about the historic incident run on a pinned baseline of the six original rule files (`baseline_rules_dir()` in `tests/conftest.py`), so adding a rule to `rules/` does not change them.
  Only a few tests use the live `rules/` folder: every rule loads, and the legit trace is fully executed.

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

### Dashboard and demo runner

- The page polls every 500 ms (SPEC section 11 says every second); the brief for milestone 5 asked for 500 ms so live traffic animates.
- The stream is ordered by arrival (rowid), not by request time, because the demo clock jumps back and forth; a divider row marks each jump of the scenario clock.
- The scenario clock is the request time of the latest reservation, not the maximum request time.
- Leaked excludes payments released later by a verdict or an admin, so a `release` verdict never counts as a leak.
- "Got an answer" for the median Jev latency means both scores in the logged response are numbers between 0 and 1.
  In stub mode the latency is 0 ms.
- A "FIRST" tag in the stream marks a payment to a beneficiary with no bank payment before it.
- The active layer card is the one whose latest decision changed last, or the one under the spotlight.
- The header shows the bank name from `BANK_NAME` and a "Fictional scenario - synthetic data" tag; there are no logos or brand colours.
- A demo caption that depends on a Jev answer waits up to 3 seconds for the Jev call to show in `/state` (the breaker commits the hold before it calls Jev); only if none appears does it say Jev gave no answer.
- In case 3 the runner pauses the attack script (SIGSTOP, then SIGCONT) while a caption is read, so the narration never lags behind the screen; `--no-hold` turns this off.
  The breaker is not affected: the request times come from the plan.
- The runner writes its traffic traces to `traces/demo_*.jsonl`, so a demo never rewrites `fixtures/legit.jsonl`.
- `scripts/demo.sh` points `INCIDENTS_DIR` at `.state/demo/incidents/` (a value equal to the default `fixtures/incidents` counts as not set, like the tokens), so a demo leaves `git status` clean.
  Outside `demo.sh` the breaker still writes to `INCIDENTS_DIR` with its default `fixtures/incidents/`.
  `TRACE_FILE_PUBLIC_DIR` keeps the webhook's `trace_file` at `fixtures/incidents/<id>.jsonl`; it is the committed trace of the same scenario, which is what the external agent can read.
- A fresh seed by `demo.sh` also empties `.state/demo/incidents/`; `--keep-data` keeps it.
- In `--auto` mode case 4 ends when no PR watcher is running, with a "No pull request" caption that follows the webhook status.
- The fallback copies the rehearsal verdict to a temp file with the current incident id, the URL from the pointer file as `pr_url`, and only the fields the endpoint accepts, before `apply_verdict.sh` sends it with `VERDICT_SOURCE=rehearsal`.
- The fallback caption comes after the breaker accepted the verdict, so a refused verdict never gets a caption.
- `verdict_source` is stored per verdict on the incident; it is `live` without the header.
  A database seeded before this column needs a fresh seed.
- Jev timeout or error: the runner compares the logged latency of a call without an answer with `JEV_TIMEOUT_S` (95 percent or more is a timeout), because the breaker logs no error text for a failed Jev call.
- At the end of case 3 the runner waits up to 4 seconds for the webhook result and then says that each further payment on the paused API key is held, with the webhook status.
- Every child process of the runner (traffic scripts, replay, PR watcher, `apply_verdict.sh`) gets its stdin from `/dev/null`, so none of them can take over the presenter's terminal.
  In a pseudo terminal without a controlling session one of them did, and the runner's next caption line failed with an I/O error.
- After the last beat plain Enter does nothing; only `q` then Enter ends the demo, so a stray Enter cannot turn the projector to "OFFLINE".
- The restart command after an error or Ctrl-C names the current case; `demo.sh` passes its own flags to the runner in `DEMO_SH_FLAGS` for it.
  When `demo.py` runs on its own, the command is `scripts/demo.py --url ... --from N`.
- `demo.sh` reads `.env` from `DEMO_ENV_FILE` when set (the tests pass an empty file) and keeps its logs and tokens in `DEMO_STATE_DIR` (default `.state/demo`).
- Environment over `.env`: `scripts/_env.sh` reads the names assigned in `.env`, remembers the values the environment already has for them, sources the file, and puts the remembered values back with `export NAME=value`.
  A variable that is set but empty counts as set.

### Milestone 6: probe, expiry, payroll

- Payroll day: the 25th of `DEMO_DATE`'s month (2026-11-25) at 09:00.
  The seed pays salaries on the 25th at 09:00 every month and the open payroll bases are for this month, so this is the natural date.
  It does not collide with anything: history ends on D-2, legit runs on D-1 daytime, soft and attack on D at night.
  On a fresh seed the burst windows hold only payroll; after legit only, it still works.
  After the attack it does not: the 09:00 request on the 25th expires the attack holds and the key is in `probe`, so the script fails and says to run on a fresh seed.
- The payroll script reads the salaries from the seeded bases instead of copying the seed's random draws, so the seed stays unchanged.
- Incident holds share one deadline (latest hold plus the limit, or `hold_until`), so every hold is older than the limit when it expires and a key that keeps sending requests while paused stays paused.
- An expired incident gets the new status `expired`, not `closed`, so it is clear that nobody decided it.
- Per payment expiry events, plus one for the incident and one for the move to `probe`.
- Probe holds use `level = hard`, like `key_paused`: Jev never releases them.
- Only a payment with no rule violation counts as clean; a Jev release in probe does not.
- New variables `PROBE_MAX_AMOUNT_AED` (1000) and `PROBE_CLEAN_PAYMENTS` (5), read once at breaker start.
- `key_state` has a new column `probe_clean`; databases from before milestone 6 need a fresh seed.

### Security hardening

- The development default of `SIM_TOKEN` is `DEV_SIM_TOKEN` in `scripts/common.py`; both services import it from there, so it is defined once.
  An empty `SIM_TOKEN` counts as not set.
- Only the breaker writes the `clock` event for an ignored `X-Sim-Time`; the bank has no event log and ignores the header silently.
  With `DEMO_MODE=0` the header is ignored without an event.
- A verdict or admin call without `X-Sim-Time` still uses the request clock, and a `release` still sends that time to the bank, now with the breaker's own `X-Sim-Token`.
- `jev_calls.released` is 1 only when the payment was really released.
  A `release` answer refused by the gate is logged with `released = 0`, its scores stay in `response_json`, and the event reads "Jev answered release for ..., hold kept: <why>".
- The Jev release gate reuses the verdict re-check, so it also needs a bank payment to the beneficiary before the hold.
  A basis carried by an `executed` reservation is now also unavailable to a `release` verdict (before, only `pending` and `released`); the bank's `used` flag already covered that case.
- The bank answers a repeated `Idempotency-Key` of the same API key with the stored payment (HTTP 200, same shape as a new one) and a key used by another API key with 409.
  `bank.payments` has a new nullable unique column `idempotency_key`; databases from before need a fresh seed.
- Restart reconciliation runs once when the breaker starts (FastAPI lifespan), timed on each row's own request clock.
  Settled rows get `decided_by = restart`; `restart_unclear` holds have `level = hard`, so Jev never releases them, and they expire like other holds.
  Without databases it is skipped with a warning on stderr.
- The load-time rule check reads the SQL as tokens and ignores comments and string literals: one statement, and the first word is `SELECT`, or `WITH` with `SELECT` as the first verb outside the parentheses.
  The authorizer at run time is the real guard.
- `scripts/demo.sh` also treats a token equal to its development default as not set, so a `.env` copied from `.env.example` still gets random tokens.
- `ALLOWED_HOSTS` values are compared without the port (Starlette `TrustedHostMiddleware`).

### Getting Devin's verdict back

- The watcher takes only pull requests from branches in the watched repository (not forks), optionally limited to `--authors`; `--allow-forks` is the explicit opt-out.
- The watcher uses the PR's `html_url` from GitHub as `pr_url`, whatever `verdict.json` says.
- Verdict file lookup: among the changed files, the `verdict.json` nearest to the repo root; paths with `..`, a leading `/` or a backslash are refused.
- A verdict file that is missing, not valid JSON, not an object or larger than 200 KB counts as "not there yet": the watcher keeps polling, because the agent may push more commits.
- A rule file that is too large or has an unsafe name is skipped with a warning and the rest are saved; `.state/pr/<number>/` is rebuilt from scratch on each run.
- `latest.json` is written once with `applied: false` after the artifacts are saved, and again with `applied: true` after the breaker accepted the verdict.
- Any breaker failure (HTTP error or not reachable) exits 4.
- Without `--authors`, any account that can push a branch to the watched repository is trusted; the fork check is the main guard, the allowlist is optional.
- `--timeout-min 0` means one poll, then exit 3 if nothing usable was found.
- A 403 counts as a rate limit only with `Retry-After` or `X-RateLimit-Remaining: 0`, the two signs GitHub gives for its primary and secondary limits.
- A GitHub API still failing at the deadline exits 5, not 3, so the runner can tell "GitHub broken" from "no pull request yet".
- `--save-as` takes only `latest` or `rehearsal`; both files have the same shape.
- The test-only option `--repo-root` moves `.state/` away from the repo.
