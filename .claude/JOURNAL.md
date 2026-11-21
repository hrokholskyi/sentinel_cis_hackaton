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
