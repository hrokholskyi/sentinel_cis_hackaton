# Graph Report - sentinel  (2026-11-21)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 609 nodes · 1376 edges · 37 communities (21 shown, 16 thin omitted)
- Extraction: 98% EXTRACTED · 2% INFERRED · 0% AMBIGUOUS · INFERRED: 34 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `4c3f4e73`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Runner
- replaylib.py
- jev_eval.py
- watch_pr.py
- sqlite3
- common.py
- json
- dashboard.py
- httpx
- changelog.py
- incidents.py
- conftest.py
- payout
- breaker/app.py
- facts.py
- engine.py
- bank/app.py
- bank/schema.sql
- verdict
- breaker/schema.sql
- demo.sh
- replay.sh
- apply_verdict.sh
- change_request.sh script
- facts.sh script
- infra_check.sh script
- _python.sh
- watch_pr.sh script

## God Nodes (most connected - your core abstractions)
1. `Runner` - 28 edges
2. `payout()` - 21 edges
3. `run()` - 18 edges
4. `GitHub` - 16 edges
5. `fake_with_verdict()` - 15 edges
6. `ts()` - 14 edges
7. `watch()` - 14 edges
8. `build()` - 14 edges
9. `verdict()` - 13 edges
10. `Db` - 12 edges

## Surprising Connections (you probably didn't know these)
- `test_jev_timeout_keeps_hold()` --calls--> `free_port()`  [INFERRED]
  tests/test_jev.py → scripts/replaylib.py
- `facts_for()` --calls--> `get_reservation()`  [EXTRACTED]
  scripts/jev_eval.py → breaker/engine.py
- `reject_hold()` --references--> `post()`  [EXTRACTED]
  breaker/app.py → tests/test_presenter.py
- `release_hold()` --references--> `post()`  [EXTRACTED]
  breaker/app.py → tests/test_presenter.py
- `resume_key()` --references--> `post()`  [EXTRACTED]
  breaker/app.py → tests/test_presenter.py

## Import Cycles
- None detected.

## Communities (37 total, 16 thin omitted)

### Community 0 - "Runner"
Cohesion: 0.10
Nodes (7): aed(), main(), pr_short(), reading_time(), Runner, utc_now(), word()

### Community 1 - "replaylib.py"
Cohesion: 0.08
Nodes (20): attack_passes(), base_env(), _exit_on_term(), free_port(), _git(), latest_incident_trace(), legit_passes(), main() (+12 more)

### Community 2 - "jev_eval.py"
Cohesion: 0.08
Nodes (27): assess(), build_facts(), _headers(), _http(), JevResult, _num(), _policy(), _post() (+19 more)

### Community 3 - "watch_pr.py"
Cohesion: 0.10
Nodes (24): apply_verdict(), candidates(), clean_verdict(), detect_repo(), find_report_path(), find_token(), find_verdict_path(), finish() (+16 more)

### Community 4 - "sqlite3"
Cohesion: 0.09
Nodes (19): wait_healthy(), LowScoreJev, run_runner(), test_case3_caption_waits_for_slow_jev(), test_demo_runner_auto_completes_all_cases(), watch_presenter(), jev_rows(), run_legit() (+11 more)

### Community 5 - "common.py"
Cohesion: 0.10
Nodes (15): invoice_description(), iso(), _legit_plan(), round50(), send_all(), at(), fresh(), iban() (+7 more)

### Community 6 - "json"
Cohesion: 0.14
Nodes (24): test_attack_pauses_key(), test_legit_traffic_all_executed(), fake_with_verdict(), FakeGitHub, do_GET(), page(), send(), gh() (+16 more)

### Community 7 - "dashboard.py"
Cohesion: 0.11
Nodes (18): clear_presenter(), build(), hold_queue(), incident_card(), layer1(), layer2(), leaked(), legit_held() (+10 more)

### Community 8 - "httpx"
Cohesion: 0.13
Nodes (22): db(), run(), test_dashboard_page_is_served(), test_jev_without_answer_shows_no_answer(), test_state_after_verdict_with_pr(), test_state_matches_database_after_demo_sequence(), post(), test_presenter_needs_admin_token() (+14 more)

### Community 9 - "changelog.py"
Cohesion: 0.14
Nodes (14): main(), changelog_text(), commits(), covered(), git(), GitError, lock_diff(), lock_text() (+6 more)

### Community 10 - "incidents.py"
Cohesion: 0.12
Nodes (13): append(), create(), iso_local(), notify(), num(), open_incident(), record_verdict(), repo_path() (+5 more)

### Community 11 - "conftest.py"
Cohesion: 0.11
Nodes (7): baseline_rules_dir(), make_stack(), make(), stack(), test_baseline_replay_leaks_six(), test_every_live_rule_loads(), test_replay_cli_on_live_rules()

### Community 12 - "payout"
Cohesion: 0.16
Nodes (14): connect(), decide_soft(), forward(), held(), _jev_warmup_failed(), now_local(), payout(), reject_hold() (+6 more)

### Community 13 - "breaker/app.py"
Cohesion: 0.14
Nodes (12): Card, Evidence, health(), index(), Panel, Payout, Presenter, require_admin() (+4 more)

### Community 14 - "facts.py"
Cohesion: 0.21
Nodes (10): bases(), beneficiary(), cli_ok(), Db, fmt(), held(), key_history(), main() (+2 more)

### Community 15 - "engine.py"
Cohesion: 0.14
Nodes (8): evaluate(), get_reservation(), insert_reservation(), key_state(), load_rules(), pick(), Result, Rule

### Community 16 - "bank/app.py"
Cohesion: 0.18
Nodes (6): connect(), health(), now_local(), num(), payout(), Payout

### Community 17 - "bank/schema.sql"
Cohesion: 0.44
Nodes (9): accounts, api_keys, bases, beneficiaries, clients, ix_bases_beneficiary, ix_payments_account, ix_payments_beneficiary (+1 more)

### Community 18 - "verdict"
Cohesion: 0.22
Nodes (6): _release(), verdict(), held_payments(), next_morning(), recheck(), request_clock()

### Community 19 - "breaker/schema.sql"
Cohesion: 0.31
Nodes (8): events, incidents, ix_reservations_account, ix_reservations_incident, ix_reservations_key, jev_calls, key_state, reservations

### Community 20 - "demo.sh"
Cohesion: 0.31
Nodes (7): BANK_URL, BREAKER_URL, port_busy(), port_of(), PYTHON, demo.sh script, wait_healthy()

## Knowledge Gaps
- **3 isolated node(s):** `BANK_URL`, `BREAKER_URL`, `PYTHON`
  These have ≤1 connection - possible missing edges. (Counts symbols only; 201 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **16 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What connects `BANK_URL`, `BREAKER_URL`, `PYTHON` to the rest of the system?**
  _3 weakly-connected nodes found - possible missing edges._
- **Should `Runner` be split into smaller, more focused modules?**
  _Cohesion score 0.10204081632653061 - nodes in this community are weakly interconnected._
- **Should `replaylib.py` be split into smaller, more focused modules?**
  _Cohesion score 0.08078231292517007 - nodes in this community are weakly interconnected._
- **Should `jev_eval.py` be split into smaller, more focused modules?**
  _Cohesion score 0.07535460992907801 - nodes in this community are weakly interconnected._
- **Should `watch_pr.py` be split into smaller, more focused modules?**
  _Cohesion score 0.10283687943262411 - nodes in this community are weakly interconnected._
- **Should `sqlite3` be split into smaller, more focused modules?**
  _Cohesion score 0.09175377468060394 - nodes in this community are weakly interconnected._
- **Should `common.py` be split into smaller, more focused modules?**
  _Cohesion score 0.09957325746799431 - nodes in this community are weakly interconnected._