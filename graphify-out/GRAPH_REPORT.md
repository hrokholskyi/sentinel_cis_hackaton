# Graph Report - sentinel  (2026-11-21)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 773 nodes · 1843 edges · 42 communities (24 shown, 18 thin omitted)
- Extraction: 98% EXTRACTED · 2% INFERRED · 0% AMBIGUOUS · INFERRED: 45 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `12a6c55b`
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

## God Nodes (most connected - your core abstractions)
1. `Runner` - 28 edges
2. `fake_with_verdict()` - 25 edges
3. `watch()` - 24 edges
4. `payout()` - 24 edges
5. `pay()` - 21 edges
6. `ts()` - 21 edges
7. `run()` - 20 edges
8. `baseline_rules_dir()` - 17 edges
9. `q()` - 17 edges
10. `GitHub` - 16 edges

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

## Communities (42 total, 18 thin omitted)

### Community 0 - "Runner"
Cohesion: 0.06
Nodes (35): wait_healthy(), baseline_rules_dir(), BankProxy, extra_breaker(), FakeJev, pay(), q(), sim() (+27 more)

### Community 1 - "replaylib.py"
Cohesion: 0.05
Nodes (40): build_cases(), _connect(), _copy_dbs(), demo_run(), evaluate(), facts_for(), facts_of_reservation(), main() (+32 more)

### Community 2 - "jev_eval.py"
Cohesion: 0.06
Nodes (24): attack_passes(), base_env(), _exit_on_term(), free_port(), _git(), latest_incident_trace(), legit_passes(), main() (+16 more)

### Community 3 - "watch_pr.py"
Cohesion: 0.08
Nodes (30): apply_verdict(), author_of(), candidates(), refuse(), check_origin(), check_shape(), clean_verdict(), detect_repo() (+22 more)

### Community 4 - "sqlite3"
Cohesion: 0.07
Nodes (21): invoice_description(), iso(), _legit_plan(), payroll_basis_id(), payroll_plan(), round50(), send_all(), at() (+13 more)

### Community 5 - "common.py"
Cohesion: 0.10
Nodes (7): aed(), main(), pr_short(), reading_time(), Runner, utc_now(), word()

### Community 6 - "json"
Cohesion: 0.12
Nodes (34): test_attack_pauses_key(), test_legit_traffic_all_executed(), fake_with_verdict(), FakeGitHub, do_GET(), page(), send(), gh() (+26 more)

### Community 7 - "dashboard.py"
Cohesion: 0.10
Nodes (19): clear_presenter(), build(), expired(), hold_queue(), incident_card(), layer1(), layer2(), leaked() (+11 more)

### Community 8 - "httpx"
Cohesion: 0.11
Nodes (19): payout(), add_event(), count_probe_clean(), evaluate(), _evaluate(), expire_holds(), get_reservation(), insert_reservation() (+11 more)

### Community 9 - "changelog.py"
Cohesion: 0.11
Nodes (17): Card, connect(), Evidence, health(), index(), _jev_warmup_failed(), lifespan(), Panel (+9 more)

### Community 10 - "incidents.py"
Cohesion: 0.12
Nodes (13): append(), create(), iso_local(), notify(), num(), open_incident(), record_verdict(), repo_path() (+5 more)

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
Cohesion: 0.16
Nodes (7): connect(), executed(), health(), now_local(), num(), payout(), Payout

### Community 15 - "engine.py"
Cohesion: 0.28
Nodes (13): bank_payments(), main_demo(), pay(), q(), run(), small(), test_admin_call_runs_expiry_on_the_request_clock(), test_hold_until_morning_and_backward_clock_delay_expiry() (+5 more)

### Community 16 - "bank/app.py"
Cohesion: 0.18
Nodes (11): expire_for_key(), now_local(), reject_hold(), release_hold(), request_clock(), resume_key(), _take_hold(), post() (+3 more)

### Community 17 - "bank/schema.sql"
Cohesion: 0.32
Nodes (11): bank(), body(), pay(), regular_plan(), reservation(), test_release_verdict_all_pass_resumes_key(), test_release_verdict_refuses_attack_without_basis(), test_release_verdict_sends_only_payments_that_pass() (+3 more)

### Community 18 - "verdict"
Cohesion: 0.21
Nodes (5): is_single_select(), load_rules(), test_breaker_does_not_start_with_a_writing_rule(), test_load_accepts_select_and_with_select(), test_load_refuses_a_rule_that_is_not_one_select()

### Community 19 - "breaker/schema.sql"
Cohesion: 0.44
Nodes (9): accounts, api_keys, bases, beneficiaries, clients, ix_bases_beneficiary, ix_payments_account, ix_payments_beneficiary (+1 more)

### Community 20 - "demo.sh"
Cohesion: 0.29
Nodes (8): BANK_URL, BREAKER_URL, new_token(), port_busy(), port_of(), PYTHON, demo.sh script, wait_healthy()

### Community 21 - "replay.sh"
Cohesion: 0.31
Nodes (8): events, incidents, ix_reservations_account, ix_reservations_incident, ix_reservations_key, jev_calls, key_state, reservations

### Community 22 - "apply_verdict.sh"
Cohesion: 0.29
Nodes (5): _release(), verdict(), held_payments(), next_morning(), request_clock()

### Community 23 - "change_request.sh script"
Cohesion: 0.33
Nodes (3): decide_soft(), release_gate(), recheck()

## Knowledge Gaps
- **3 isolated node(s):** `BANK_URL`, `BREAKER_URL`, `PYTHON`
  These have ≤1 connection - possible missing edges. (Counts symbols only; 255 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **18 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `payout()` connect `httpx` to `replaylib.py`, `changelog.py`, `incidents.py`, `bank/app.py`, `change_request.sh script`, `infra_check.sh script`?**
  _High betweenness centrality (0.018) - this node is a cross-community bridge._
- **What connects `BANK_URL`, `BREAKER_URL`, `PYTHON` to the rest of the system?**
  _3 weakly-connected nodes found - possible missing edges._
- **Should `Runner` be split into smaller, more focused modules?**
  _Cohesion score 0.06493506493506493 - nodes in this community are weakly interconnected._
- **Should `replaylib.py` be split into smaller, more focused modules?**
  _Cohesion score 0.05189189189189189 - nodes in this community are weakly interconnected._
- **Should `jev_eval.py` be split into smaller, more focused modules?**
  _Cohesion score 0.05505952380952381 - nodes in this community are weakly interconnected._
- **Should `watch_pr.py` be split into smaller, more focused modules?**
  _Cohesion score 0.08408953418027829 - nodes in this community are weakly interconnected._
- **Should `sqlite3` be split into smaller, more focused modules?**
  _Cohesion score 0.07088989441930618 - nodes in this community are weakly interconnected._