# Graph Report - sentinel  (2026-11-21)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 867 nodes · 2076 edges · 50 communities (29 shown, 21 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 53 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `97c640aa`
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
- 02_amount_above_typical.sql
- 03_first_payment_above_limit.sql
- 04_burst_count_5m.sql
- 05_burst_sum_1h.sql
- 06_drain_1h.sql
- fixture
- A local webhook endpoint that records every POST and answers with `status`.
- 05_burst_sum_1h.sql
- 06_drain_1h.sql
- 06_drain_1h.sql

## God Nodes (most connected - your core abstractions)
1. `Runner` - 35 edges
2. `fake_with_verdict()` - 31 edges
3. `watch()` - 31 edges
4. `payout()` - 24 edges
5. `baseline_rules_dir()` - 21 edges
6. `pay()` - 21 edges
7. `run()` - 21 edges
8. `ts()` - 21 edges
9. `GitHub` - 17 edges
10. `q()` - 17 edges

## Surprising Connections (you probably didn't know these)
- `test_jev_timeout_keeps_hold()` --calls--> `wait_healthy()`  [INFERRED]
  tests/test_jev.py → scripts/replaylib.py
- `extra_breaker()` --calls--> `free_port()`  [INFERRED]
  tests/hardening_helpers.py → scripts/replaylib.py
- `test_jev_timeout_keeps_hold()` --calls--> `free_port()`  [INFERRED]
  tests/test_jev.py → scripts/replaylib.py
- `evaluate()` --calls--> `assess()`  [EXTRACTED]
  scripts/jev_eval.py → breaker/jev.py
- `facts_for()` --calls--> `get_reservation()`  [EXTRACTED]
  scripts/jev_eval.py → breaker/engine.py

## Import Cycles
- None detected.

## Communities (50 total, 21 thin omitted)

### Community 0 - "Runner"
Cohesion: 0.06
Nodes (20): aed(), clean_verdict(), flush_input(), jev_failure(), jev_kept_text(), main(), no_pr_caption(), payments() (+12 more)

### Community 1 - "replaylib.py"
Cohesion: 0.07
Nodes (34): wait_healthy(), BankProxy, extra_breaker(), FakeJev, pay(), q(), sim(), wait_for() (+26 more)

### Community 2 - "jev_eval.py"
Cohesion: 0.08
Nodes (31): apply_verdict(), author_of(), candidates(), refuse(), check_origin(), check_shape(), clean_verdict(), detect_repo() (+23 more)

### Community 3 - "watch_pr.py"
Cohesion: 0.09
Nodes (45): rows(), test_payroll_burst_is_held_then_released_by_verdict(), test_payroll_plan_matches_the_seeded_payroll(), test_attack_pauses_key(), test_legit_traffic_all_executed(), fake_with_verdict(), FakeGitHub, do_GET() (+37 more)

### Community 4 - "sqlite3"
Cohesion: 0.07
Nodes (34): build_cases(), _connect(), _copy_dbs(), demo_run(), evaluate(), facts_for(), facts_of_reservation(), main() (+26 more)

### Community 5 - "common.py"
Cohesion: 0.07
Nodes (21): invoice_description(), iso(), _legit_plan(), payroll_basis_id(), payroll_plan(), round50(), send_all(), at() (+13 more)

### Community 6 - "json"
Cohesion: 0.08
Nodes (20): attack_passes(), base_env(), _exit_on_term(), free_port(), _git(), latest_incident_trace(), legit_passes(), main() (+12 more)

### Community 7 - "dashboard.py"
Cohesion: 0.10
Nodes (21): Card, connect(), Evidence, expire_for_key(), _jev_warmup_failed(), lifespan(), now_local(), Panel (+13 more)

### Community 8 - "httpx"
Cohesion: 0.10
Nodes (19): clear_presenter(), build(), expired(), hold_queue(), incident_card(), layer1(), layer2(), leaked() (+11 more)

### Community 9 - "changelog.py"
Cohesion: 0.11
Nodes (15): payout(), append(), create(), iso_local(), notify(), num(), open_incident(), public_trace_file() (+7 more)

### Community 10 - "incidents.py"
Cohesion: 0.12
Nodes (15): add_event(), count_probe_clean(), evaluate(), _evaluate(), expire_holds(), insert_reservation(), _num(), pick() (+7 more)

### Community 11 - "conftest.py"
Cohesion: 0.13
Nodes (14): assess(), build_facts(), _headers(), _http(), JevResult, _num(), _policy(), _post() (+6 more)

### Community 12 - "payout"
Cohesion: 0.16
Nodes (14): main(), changelog_text(), commits(), covered(), git(), GitError, lock_diff(), lock_text() (+6 more)

### Community 13 - "breaker/app.py"
Cohesion: 0.21
Nodes (10): bases(), beneficiary(), cli_ok(), Db, fmt(), held(), key_history(), main() (+2 more)

### Community 14 - "facts.py"
Cohesion: 0.13
Nodes (10): decide_soft(), _release(), release_gate(), verdict(), get_reservation(), key_state(), held_payments(), next_morning() (+2 more)

### Community 15 - "engine.py"
Cohesion: 0.16
Nodes (7): connect(), executed(), health(), now_local(), num(), payout(), Payout

### Community 16 - "bank/app.py"
Cohesion: 0.28
Nodes (13): bank_payments(), main_demo(), pay(), q(), run(), small(), test_admin_call_runs_expiry_on_the_request_clock(), test_hold_until_morning_and_backward_clock_delay_expiry() (+5 more)

### Community 17 - "bank/schema.sql"
Cohesion: 0.19
Nodes (8): demo_sh_env(), port_free(), remove_runner_traces(), test_captions_follow_the_state(), test_clear_is_silent_when_the_breaker_is_gone(), test_demo_sh_keep_data_continues_after_a_crash(), test_demo_sh_keep_data_without_databases_refuses(), test_flush_input_discards_typed_lines_on_a_terminal()

### Community 18 - "verdict"
Cohesion: 0.29
Nodes (12): bank(), body(), pay(), regular_plan(), reservation(), test_facts_held_reads_the_incident(), test_release_verdict_all_pass_resumes_key(), test_release_verdict_refuses_attack_without_basis() (+4 more)

### Community 19 - "breaker/schema.sql"
Cohesion: 0.19
Nodes (11): BANK_URL, BREAKER_URL, DEMO_SH_FLAGS, DEMO_STATE_DIR, new_token(), port_busy(), port_of(), PYTHON (+3 more)

### Community 20 - "demo.sh"
Cohesion: 0.27
Nodes (4): runner_argv(), Terminal, test_ctrl_c_prints_the_restart_command(), test_guided_run_with_the_rehearsal_fallback()

### Community 21 - "replay.sh"
Cohesion: 0.21
Nodes (5): is_single_select(), load_rules(), test_breaker_does_not_start_with_a_writing_rule(), test_load_accepts_select_and_with_select(), test_load_refuses_a_rule_that_is_not_one_select()

### Community 22 - "apply_verdict.sh"
Cohesion: 0.23
Nodes (5): baseline_rules_dir(), presenter_states(), run_runner(), test_case3_caption_waits_for_slow_jev(), test_demo_runner_auto_completes_all_cases()

### Community 23 - "change_request.sh script"
Cohesion: 0.22
Nodes (4): test_env_file_fills_in_only_what_is_missing(), test_baseline_replay_leaks_six(), test_every_live_rule_loads(), test_replay_cli_on_live_rules()

### Community 24 - "facts.sh script"
Cohesion: 0.44
Nodes (9): accounts, api_keys, bases, beneficiaries, clients, ix_bases_beneficiary, ix_payments_account, ix_payments_beneficiary (+1 more)

### Community 25 - "infra_check.sh script"
Cohesion: 0.22
Nodes (3): make_stack(), make(), stack()

### Community 26 - "_python.sh"
Cohesion: 0.31
Nodes (8): events, incidents, ix_reservations_account, ix_reservations_incident, ix_reservations_key, jev_calls, key_state, reservations

### Community 27 - "watch_pr.sh script"
Cohesion: 0.31
Nodes (8): attack(), captions(), make_pr_folder(), post_verdict(), test_apply_verdict_sh_sends_the_rehearsal_header(), test_case5_shows_only_before_when_the_held_pr_has_no_saved_rules(), test_rehearsal_verdict_has_no_pause_to_pr_minutes(), verdict_body()

### Community 28 - "bank/__init__.py"
Cohesion: 0.33
Nodes (4): health(), index(), state(), pause_to_pr_min()

## Knowledge Gaps
- **6 isolated node(s):** `BANK_URL`, `BREAKER_URL`, `DEMO_SH_FLAGS`, `DEMO_STATE_DIR`, `PYTHON` (+1 more)
  These have ≤1 connection - possible missing edges. (Counts symbols only; 284 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **21 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `payout()` connect `changelog.py` to `sqlite3`, `dashboard.py`, `incidents.py`, `facts.py`, `03_first_payment_above_limit.sql`?**
  _High betweenness centrality (0.019) - this node is a cross-community bridge._
- **What connects `BANK_URL`, `BREAKER_URL`, `DEMO_SH_FLAGS` to the rest of the system?**
  _6 weakly-connected nodes found - possible missing edges._
- **Should `Runner` be split into smaller, more focused modules?**
  _Cohesion score 0.05917721518987342 - nodes in this community are weakly interconnected._
- **Should `replaylib.py` be split into smaller, more focused modules?**
  _Cohesion score 0.07081377151799687 - nodes in this community are weakly interconnected._
- **Should `jev_eval.py` be split into smaller, more focused modules?**
  _Cohesion score 0.07680491551459294 - nodes in this community are weakly interconnected._
- **Should `watch_pr.py` be split into smaller, more focused modules?**
  _Cohesion score 0.08806963645673323 - nodes in this community are weakly interconnected._
- **Should `sqlite3` be split into smaller, more focused modules?**
  _Cohesion score 0.06892655367231638 - nodes in this community are weakly interconnected._