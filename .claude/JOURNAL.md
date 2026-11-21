# Decision log

## 2026-11-21 - Build split across agents
M1+M2 (bank, seed, proxy, rule engine, attack) go to one Opus agent because they share the same files and M2 rewrites the M1 proxy.
M3 (Jev) runs after it, then M4 (incident tooling) and M5 (dashboard) run in parallel in worktrees.
Rejected: one agent per milestone from the start, because M1 alone is too small and its proxy code is thrown away by M2.

## 2026-11-21 - Counters include the current payment
The reservation is inserted first, then the rules run, inside one `BEGIN IMMEDIATE` transaction.
Why: SPEC section 8 says payment 9 trips `burst_count_5m` with the rule text `COUNT(*) > 8`, and the webhook example shows `count: 9`.
Rejected: evaluate first and insert after, because then the key would pause on payment 10.

## 2026-11-21 - Fixed demo date and time windows with an upper bound
Demo day is fixed at 2026-11-21 (same date as the webhook example in SPEC): legit traffic runs on 2026-11-20 daytime, soft and attack on 2026-11-21 night.
Every windowed rule also filters `created_at <= :now`.
Why: the demo order is legit, soft (02:10), attack (02:00), so the simulated clock goes backwards and later rows must not count in earlier windows.
Timestamps are stored as local Gulf time, `YYYY-MM-DD HH:MM:SS`, so SQLite `datetime()` works on them.

## 2026-11-21 - Hold expiry deferred to M6
No hold expiry before milestone 6.
Why: with a simulated clock a real-time expiry job would reject every held payment at once and break the demo.

## 2026-11-21 - Pause latency uses the request clock
"Milliseconds from first violation to pause" is the request-clock gap (simulated time in the demo) plus the real processing time of the tripping request.
Rejected: pure wall clock, because the traffic scripts compress real time and the number would depend on script speed.

## 2026-11-21 - Jev endpoint in SPEC is wrong
SPEC section 7 gives `https://api.typesafe.ai/v1/decisions` (taken from Portkey gateway docs).
The TypeSafe docs (docs.typesafe.ai/api/system-one.md) say the direct endpoint is `POST https://api.typesafe.ai/v1/systemone`, auth `Authorization: Bearer`, so that becomes the `JEV_URL` default.
`state` is sent as a JSON object, not a JSON string, because the docs accept both and prefer named fields.
Model stays pinned at `jev-1.13.0` (valid; `jev-1.14.0` is newer) and there are no retries, because the policy is 1 second then keep the hold.
Not verified against the live API: there is no `TYPESAFE_API_KEY` in this environment, and latency is undocumented, so the 1 second timeout may be tight.

## 2026-11-21 - Graphify graph will be committed
Roman approved using graphify.
The Devin prompt must stay unchanged and its step 5 reads `graphify-out/`, so the graph is generated after the code is stable (after M4, refreshed after M5) and committed.
Rejected: leaving the prompt pointing at a folder that does not exist, because Devin would lose time on step 5 during the live demo.

## 2026-11-21 - Demo order is fixed: legit, soft, attack
Found during M2: a soft-case payment sent after the attack is a hard stop by `burst_sum_1h`, because the attack's held payments (AED 423,000) are still in its 1 hour window.
The soft scenario only works before the attack, on a fresh seed after legit traffic.
To rerun the demo, reseed first.

## 2026-11-21 - M3 and M4 run in parallel worktrees
Changed from the first plan (M3 first, then M4 and M5) to save time: M3 (Jev) and M4 (incident tooling) touch mostly different files.
M5 (dashboard) waits for both, because it reads `jev_calls` and the verdict fields.
Cost: a likely small merge conflict in `breaker/app.py`, `README.md` and `.env.example`.

## 2026-11-21 - Investigator scripts must work from a bare clone
Wrong assumption in SPEC section 10: `facts.sh` and `infra_check.sh` read the live databases, but Devin only has a git clone on a Linux machine.
Decision: the scripts rebuild the incident state from the deterministic seed plus the committed traces when no database exists, and they are portable across macOS and Ubuntu.
The verdict comes back by a human running `scripts/apply_verdict.sh` with the `verdict.json` from the PR.

## 2026-11-21 - CHANGELOG.md is written only by a script
`infra_check.sh` needs `CHANGELOG.md`, and the global rule forbids editing it by hand.
Decision: `scripts/change_request.sh` is the only writer of `CHANGELOG.md` and `rules/rules.lock`.
Every commit of this project is younger than 24 hours, so this script must run as the last commit before the demo, or Devin's step 2 will return `escalate_bank_security` and skip the client investigation.

## 2026-11-21 - Keep the hand-written toy bank
Roman asked about a ready-made bank from GitHub.
Rejected: the bank is 132 lines, already verified, and must have our schema, the simulated clock header and the intentional gap; adapting a third-party project would cost more than it saves.

## 2026-11-21 - Live Jev works, but does not release the demo soft case
First real calls to `POST https://api.typesafe.ai/v1/systemone` with `jev-1.13.0`: endpoint, auth and response shape are confirmed.
The legitimate soft case scores 0.78 and 0.85, below the 0.9 thresholds, so live mode keeps it on hold; the attack case scores 0.22 and 0.15, which is correct.
Latency was 284 to 966 ms against a 1 second timeout, so it is tight.
Decision: a tuning agent improves the facts and the question wording first (honest aggregates, no hidden verdict), adds a persistent HTTP client and a warm-up, and builds `scripts/jev_eval.py`.
Rejected for now: lowering the thresholds just to pass the demo; it stays an option only with score distributions as evidence.
The key lives in the gitignored `.env`.

## 2026-11-21 - Devin is started by an Automation webhook, the PR is found through GitHub
Research on docs.devin.ai: Devin has Automations with a Webhook trigger; the caller sends the secret in `X-Webhook-Secret` and the payload is appended to the stored prompt.
That is exactly what SPEC section 9 assumed, so the breaker needs no extra sender code.
Rejected: the v3 sessions API (`POST /v3/organizations/{org_id}/sessions`), because it needs an org id, a service user key and a second sender in the breaker; it stays the fallback if the automation does not work.
The webhook call does not return a session id, and Devin has no completion callback, so the PR is detected by polling GitHub for a new pull request that contains `verdict.json`, then the verdict is applied to the breaker.
Not verified: nothing was called on the Devin side yet, and time from trigger to PR is undocumented.

## 2026-11-21 - Demo logistics decided by Roman
Verdict return: a watcher polls GitHub for Devin's PR, reads `verdict.json` and applies it (rejected: applying by hand on stage).
Devin on stage: start it live and fall back to a PR from a rehearsal run.
Rehearsal: the main agent may trigger one real Devin run once M4 is merged and the webhook URL is in `.env`; budget is about 200 USD of Devin credits.
The browser preview stays in the temp folder, not in the repo.

## 2026-11-21 - Real names in the preview, against SPEC section 1
Roman asked for the bank to be shown as ADCB and the client as Microsoft, which overrides the SPEC rule "no real banks, names".
Applied to the preview page only for now, text only, no logos, with a "fictional scenario" tag.
Open: whether the seed data and the real dashboard get the same names.

## 2026-11-21 - Real dashboard gets real names and a guided demo runner
Roman chose: the real dashboard shows ADCB and Microsoft and has the same subtitles and spotlight as the preview.
A one-command demo runner drives it: it pushes the caption and spotlight to the breaker, runs the real traffic scripts, and waits for Enter between beats.
This goes beyond SPEC sections 11 and 13 (no subtitles, fictional client) by Roman's decision.
The client rename (name and ids, with regenerated fixtures) runs as its own task after M4 and the Jev tuning are merged, because both touch the seed files.

## 2026-11-21 - Live Jev tuned, default model moved to jev-1.14.0
Result on the real API: the soft demo case is released 20 of 20 times (0.98 and 0.99), seven must-hold cases are held 20 of 20 times, including a prompt injection in the payment purpose; rechecked by the main agent with `scripts/jev_eval.py --n 3`.
What worked: a `criteria` true/false pair on each question, a `description` column on `bank.bases` so the purpose is compared with the approved record, and facts such as `prior_payments_last_24h` and `amount_within_prior_range`.
What failed: an "average interval between payments" fact pulled the legit score down to 0.88, so it was dropped; better facts alone, without `criteria`, only reached 0.70.
Thresholds stay 0.9 and the timeout stays 1 second: warm p95 is about 290 ms, because the client is now persistent with a warm-up at start (httpx closes idle connections after 5 s by default, the idle limit is now 300 s).
Deviation from SPEC: the default model is `jev-1.14.0` instead of `jev-1.13.0`, because 1.13.0 passes with only 0.04 of margin on the legit case.
The first call after about 70 s idle can take about 520 ms (reconnect); still inside the timeout, no keep-alive ping added.

## 2026-11-21 - M4 merged without its change request commit
The M4 branch was cherry-picked, so commit hashes changed and its `CHANGELOG.md` entry would not match.
`scripts/change_request.sh` runs once on main as the last commit before the demo instead.
A stale `breaker.db` from an older schema makes `facts.sh` fail with "no such column"; reseed after pulling schema changes.

## 2026-11-21 - Existing tests would break when Devin adds its rule
Found while reviewing the merge: most tests assert the leak (6 executed, 36 held) with the live `rules/` folder, and `tests/test_replay.py` asserts that replay exits 1.
As soon as Devin adds a rule that closes the gap, those tests fail, while its prompt forbids weakening or deleting tests, so it would get stuck or break its limits.
Decision: tests about the historic incident run on a pinned baseline of the six original rule files; tests about the current rule set only check that all rules load and legit traffic passes.
To do right after the dashboard branch is merged, because that branch adds tests and the `--rules-dir` replay option.

## 2026-11-21 - Dashboard merged, full demo run verified with live Jev
`scripts/demo.sh --no-webhook --auto` ran end to end against the real TypeSafe API: soft case released at 0.98 and 0.99 in 268 ms, attack payments held at 0.02 and 0.01, 6 leaked, 36 held, key paused, 4002 ms.
Added `--no-webhook` to `demo.sh`, because it loads `.env` and would otherwise start a paid Devin session on every rehearsal.
Bug found: the runner caption said "Jev was not asked" while Jev had answered; the runner read `/state` before the Jev call was logged (the hold is committed first, the answer arrives about 250 ms later).

## 2026-11-21 - Incident trace is recorded in demo order
The committed `fixtures/incidents/inc_0001.jsonl` now comes from a legit, soft, attack run, so a stage run rewrites it byte for byte and the git tree stays clean.
Rejected: pointing `INCIDENTS_DIR` elsewhere in `demo.sh`, because the webhook's `trace_file` must name a path that exists in Devin's clone.
Cost: reservation ids in the trace are one higher than in a legit, attack run, which broke one test that compared ids; the test now ignores ids.

## 2026-11-21 - Graphify trial
`graphify extract . --code-only` plus `graphify cluster-only .` with `GRAPHIFY_BACKEND=none` works offline in seconds: 599 nodes, 1356 edges, relative paths only.
The graph answers the Devin prompt's step 5: `forward()` in `breaker/app.py` is the single function that sends money to the bank, called by `payout()`, `_release()` and `release_hold()`.
Community names stay "Community N" placeholders without an LLM backend; accepted.

## 2026-11-21 - Product renamed to TrustGap for the presentation
Roman renamed the product; applied to the dashboard, the preview page, the runner captions, README and AGENTS.md.
`SPEC.md` and `docs/devin_prompt.md` keep their text; the repo, folders and Python packages keep their names.

## 2026-11-21 - Devin rehearsal fired
Everything Devin needs was pushed first: pinned baseline tests, `AGENTS.md`, the graphify graph, and the change log commit, and a fresh clone gave `infra check: clean`.
The attack at 10:35 UTC fired the automation webhook and Devin answered HTTP 200.
A watcher polls GitHub for the PR for up to 45 minutes; the stack on ports 8000 and 8001 stays up so the verdict can be applied.
Constraint found: agent worktrees are created from the last pushed commit, not from local `main`, so push before spawning a worktree agent.

## 2026-11-21 - Milestone 6 and a review round, five Opus agents in parallel
Roman asked for the optional milestone 6 and for Opus reviews.
Split by file ownership to avoid conflicts: M6 owns the engine and new scripts; the UI agent owns `breaker/static/index.html`; the wording agent owns the captions in `scripts/demo.py` and the docs; the code and security review and the stage robustness review are read-only and report findings.
Fixes from the two read-only reviews go to one fix agent after M6 is merged.
Decision for M6: hold expiry is lazy and driven by the request clock only, never by wall-clock time or by `GET /state`, because the simulated clock jumps in the demo.
Every agent is told never to load the real `.env`: a rehearsal run with the real webhook starts a paid Devin session.
