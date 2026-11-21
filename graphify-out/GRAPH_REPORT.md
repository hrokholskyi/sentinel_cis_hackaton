# Graph Report - sentinel  (2026-11-21)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 681 nodes · 1573 edges · 39 communities (23 shown, 16 thin omitted)
- Extraction: 98% EXTRACTED · 2% INFERRED · 0% AMBIGUOUS · INFERRED: 35 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `809c4a79`
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
- bank/__init__.py
- 01_off_hours_amount.sql

## God Nodes (most connected - your core abstractions)
1. `Runner` - 28 edges
2. `fake_with_verdict()` - 25 edges
3. `payout()` - 24 edges
4. `watch()` - 24 edges
5. `ts()` - 21 edges
6. `run()` - 20 edges
7. `GitHub` - 16 edges
8. `build()` - 15 edges
9. `verdict()` - 14 edges
10. `candidates()` - 13 edges

## Surprising Connections (you probably didn't know these)
- `test_jev_timeout_keeps_hold()` --calls--> `free_port()`  [INFERRED]
  tests/test_jev.py → scripts/replaylib.py
- `test_jev_timeout_keeps_hold()` --calls--> `wait_healthy()`  [INFERRED]
  tests/test_jev.py → scripts/replaylib.py
- `facts_for()` --calls--> `get_reservation()`  [EXTRACTED]
  scripts/jev_eval.py → breaker/engine.py
- `resume_key()` --references--> `post()`  [EXTRACTED]
  breaker/app.py → tests/test_presenter.py
- `set_presenter()` --references--> `post()`  [EXTRACTED]
  breaker/app.py → tests/test_presenter.py

## Import Cycles
- None detected.

## Communities (39 total, 16 thin omitted)

### Community 0 - "Runner"
Cohesion: 0.08
Nodes (30): apply_verdict(), author_of(), candidates(), refuse(), check_origin(), check_shape(), clean_verdict(), detect_repo() (+22 more)

### Community 1 - "replaylib.py"
Cohesion: 0.07
Nodes (24): connect(), health(), now_local(), num(), payout(), Payout, LowScoreJev, run_runner() (+16 more)

### Community 2 - "jev_eval.py"
Cohesion: 0.07
Nodes (21): invoice_description(), iso(), _legit_plan(), payroll_basis_id(), payroll_plan(), round50(), send_all(), at() (+13 more)

### Community 3 - "watch_pr.py"
Cohesion: 0.07
Nodes (27): assess(), build_facts(), _headers(), _http(), JevResult, _num(), _policy(), _post() (+19 more)

### Community 4 - "sqlite3"
Cohesion: 0.10
Nodes (7): aed(), main(), pr_short(), reading_time(), Runner, utc_now(), word()

### Community 5 - "common.py"
Cohesion: 0.08
Nodes (21): attack_passes(), base_env(), _exit_on_term(), free_port(), _git(), latest_incident_trace(), legit_passes(), main() (+13 more)

### Community 6 - "json"
Cohesion: 0.12
Nodes (34): test_attack_pauses_key(), test_legit_traffic_all_executed(), fake_with_verdict(), FakeGitHub, do_GET(), page(), send(), gh() (+26 more)

### Community 7 - "dashboard.py"
Cohesion: 0.10
Nodes (19): clear_presenter(), build(), expired(), hold_queue(), incident_card(), layer1(), layer2(), leaked() (+11 more)

### Community 8 - "httpx"
Cohesion: 0.13
Nodes (23): db(), run(), test_dashboard_page_is_served(), test_jev_without_answer_shows_no_answer(), test_state_after_verdict_with_pr(), test_state_matches_database_after_demo_sequence(), bank_payments(), main_demo() (+15 more)

### Community 9 - "changelog.py"
Cohesion: 0.16
Nodes (14): main(), changelog_text(), commits(), covered(), git(), GitError, lock_diff(), lock_text() (+6 more)

### Community 10 - "incidents.py"
Cohesion: 0.11
Nodes (7): baseline_rules_dir(), make_stack(), make(), stack(), test_baseline_replay_leaks_six(), test_every_live_rule_loads(), test_replay_cli_on_live_rules()

### Community 11 - "conftest.py"
Cohesion: 0.14
Nodes (13): Card, Evidence, expire_for_key(), _jev_warmup_failed(), now_local(), Panel, Payout, Presenter (+5 more)

### Community 12 - "payout"
Cohesion: 0.13
Nodes (11): append(), iso_local(), notify(), num(), record_verdict(), repo_path(), send_webhook(), trace_line() (+3 more)

### Community 13 - "breaker/app.py"
Cohesion: 0.19
Nodes (15): rows(), test_payroll_burst_is_held_then_released_by_verdict(), test_payroll_plan_matches_the_seeded_payroll(), bank(), body(), pay(), regular_plan(), reservation() (+7 more)

### Community 14 - "facts.py"
Cohesion: 0.21
Nodes (10): bases(), beneficiary(), cli_ok(), Db, fmt(), held(), key_history(), main() (+2 more)

### Community 15 - "engine.py"
Cohesion: 0.15
Nodes (10): _release(), verdict(), add_event(), count_probe_clean(), expire_holds(), set_key_state(), held_payments(), next_morning() (+2 more)

### Community 16 - "bank/app.py"
Cohesion: 0.17
Nodes (8): evaluate(), load_rules(), _num(), pick(), probe_gate(), _reject_expired(), Result, Rule

### Community 17 - "bank/schema.sql"
Cohesion: 0.18
Nodes (7): decide_soft(), payout(), get_reservation(), insert_reservation(), key_state(), create(), open_incident()

### Community 18 - "verdict"
Cohesion: 0.44
Nodes (9): accounts, api_keys, bases, beneficiaries, clients, ix_bases_beneficiary, ix_payments_account, ix_payments_beneficiary (+1 more)

### Community 19 - "breaker/schema.sql"
Cohesion: 0.25
Nodes (5): connect(), health(), index(), state(), pause_to_pr_min()

### Community 20 - "demo.sh"
Cohesion: 0.31
Nodes (6): forward(), held(), reject_hold(), release_hold(), _take_hold(), set_status()

### Community 21 - "replay.sh"
Cohesion: 0.31
Nodes (8): events, incidents, ix_reservations_account, ix_reservations_incident, ix_reservations_key, jev_calls, key_state, reservations

### Community 22 - "apply_verdict.sh"
Cohesion: 0.31
Nodes (7): BANK_URL, BREAKER_URL, port_busy(), port_of(), PYTHON, demo.sh script, wait_healthy()

## Knowledge Gaps
- **3 isolated node(s):** `BANK_URL`, `BREAKER_URL`, `PYTHON`
  These have ≤1 connection - possible missing edges. (Counts symbols only; 223 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **16 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `payout()` connect `bank/schema.sql` to `replaylib.py`, `conftest.py`, `payout`, `engine.py`, `bank/app.py`, `breaker/schema.sql`, `demo.sh`?**
  _High betweenness centrality (0.022) - this node is a cross-community bridge._
- **What connects `BANK_URL`, `BREAKER_URL`, `PYTHON` to the rest of the system?**
  _3 weakly-connected nodes found - possible missing edges._
- **Should `Runner` be split into smaller, more focused modules?**
  _Cohesion score 0.08408953418027829 - nodes in this community are weakly interconnected._
- **Should `replaylib.py` be split into smaller, more focused modules?**
  _Cohesion score 0.06708595387840671 - nodes in this community are weakly interconnected._
- **Should `jev_eval.py` be split into smaller, more focused modules?**
  _Cohesion score 0.07039187227866474 - nodes in this community are weakly interconnected._
- **Should `watch_pr.py` be split into smaller, more focused modules?**
  _Cohesion score 0.07183673469387755 - nodes in this community are weakly interconnected._
- **Should `sqlite3` be split into smaller, more focused modules?**
  _Cohesion score 0.10204081632653061 - nodes in this community are weakly interconnected._