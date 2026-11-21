"""Milestone 5 acceptance: after legit, soft and attack, every dashboard number in /state
matches the databases."""

import sqlite3
import statistics

import httpx


def run(stack, script, *args):
    r = stack.run_script(script, "--delay", "0", "--trace", str(stack.tmp / f"{script}.jsonl"),
                         *args)
    assert r.returncode == 0, r.stdout + r.stderr


def db(stack):
    conn = sqlite3.connect(stack.env["BREAKER_DB"])
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ? AS bank", (stack.env["BANK_DB"],))
    return conn


def test_dashboard_page_is_served(stack):
    r = httpx.get(stack.breaker_url + "/", timeout=5)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "Night Circuit Breaker" in r.text and "/state" in r.text
    assert "\u2014" not in r.text  # no em dash anywhere on the page
    for cdn in ("http://", "https://"):
        assert f'src="{cdn}' not in r.text and f'href="{cdn}' not in r.text


def test_state_before_any_traffic(stack):
    s = stack.get("/state")
    assert s["bank_name"] == "ADCB"
    assert s["client"]["name"] == "Al Noor Logistics LLC"
    assert s["key"]["api_key_id"] == "key_alnoor_1" and s["key"]["state"] == "normal"
    assert s["clock"] is None and s["stream"] == [] and s["hold_queue"] == []
    assert s["incident"] is None
    assert s["counters"]["released_by_jev"]["median_latency_ms"] is None
    assert s["layers"]["layer1"]["last"] is None and s["layers"]["layer2"]["last"] is None
    assert s["layers"]["layer3"]["webhook"]["status"] == "not_configured"
    assert s["presenter"]["active"] is False


def test_state_matches_database_after_demo_sequence(stack):
    run(stack, "traffic_legit.py")
    run(stack, "traffic_soft.py")
    run(stack, "traffic_attack.py")
    s = stack.get("/state")
    c = s["counters"]
    conn = db(stack)

    # Demo values.
    assert c["stopped"] == {"count": 36, "aed": 423000}
    assert c["leaked"] == {"count": 6, "aed": 27000}
    assert c["legit_held"]["count"] == 0
    assert c["released_by_jev"]["count"] == 1
    assert s["key"]["state"] == "paused"
    assert len(s["hold_queue"]) == 15
    # The demo writes only 7 events (Jev x3, client asked to confirm x2, incident, pause).
    n_events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert len(s["events"]) == min(10, n_events) == 7
    assert len(s["stream"]) == 60
    assert s["clock"] == "2026-11-21 02:04:10"  # last attack payment

    # Every counter against direct SQL.
    held = conn.execute("SELECT COUNT(*), SUM(amount_aed) FROM reservations"
                        " WHERE status = 'held'").fetchone()
    assert (c["stopped"]["count"], c["stopped"]["aed"]) == (held[0], held[1])
    inc = conn.execute("SELECT * FROM incidents ORDER BY id DESC LIMIT 1").fetchone()
    leak = conn.execute("SELECT COUNT(*), SUM(amount_aed) FROM reservations WHERE status = 'executed'"
                        " AND api_key_id = ? AND created_at >= datetime(?, '-1 hour')"
                        " AND created_at <= ?",
                        (inc["api_key_id"], inc["tripped_at"], inc["tripped_at"])).fetchone()
    assert (c["leaked"]["count"], c["leaked"]["aed"]) == (leak[0], leak[1])
    # The soft payment (02:10) and the legit day are executed but not leaked.
    assert conn.execute("SELECT status FROM reservations WHERE amount_aed = 48000").fetchone()[0] \
        == "executed"
    # Legitimate held: held payments to a beneficiary with prior payments and a matching basis.
    legit = conn.execute(
        "SELECT COUNT(*) FROM reservations r WHERE r.status = 'held'"
        " AND EXISTS (SELECT 1 FROM bank.payments p WHERE p.beneficiary_id = r.beneficiary_id"
        "             AND p.created_at < r.created_at)"
        " AND EXISTS (SELECT 1 FROM bank.bases b WHERE b.beneficiary_id = r.beneficiary_id"
        "             AND b.approved = 1 AND b.used = 0 AND b.amount_aed = r.amount_aed)"
    ).fetchone()[0]
    assert c["legit_held"]["count"] == legit == 0
    jev = conn.execute("SELECT released, latency_ms, response_json FROM jev_calls").fetchall()
    assert c["released_by_jev"]["count"] == sum(r["released"] for r in jev) == 1
    answered = [r["latency_ms"] for r in jev if r["response_json"]]
    assert len(answered) == 3
    assert c["released_by_jev"]["median_latency_ms"] == round(statistics.median(answered))
    assert c["violation_to_pause_ms"] == inc["violation_to_pause_ms"]
    assert 4000 <= c["violation_to_pause_ms"] < 5000

    # Stream: newest first, names from bank.beneficiaries, outcomes and rules from reservations.
    rows = conn.execute("SELECT r.*, b.name FROM reservations r JOIN bank.beneficiaries b"
                        " ON b.id = r.beneficiary_id ORDER BY r.rowid DESC LIMIT 60").fetchall()
    assert [x["id"] for x in s["stream"]] == [r["id"] for r in rows]
    for x, r in zip(s["stream"], rows):
        assert (x["beneficiary_name"], x["amount_aed"], x["status"], x["rule_id"], x["level"]) == \
            (r["name"], r["amount_aed"], r["status"], r["rule_id"], r["level"])
    assert s["stream"][0]["beneficiary_name"] == "Eastport Supply Solutions"
    assert s["reservations"]["executed"] == 42 + 1 + 6 and s["reservations"]["held"] == 36

    # Hold queue: the last 15 held, with rule id and level.
    q = conn.execute("SELECT id, rule_id, level FROM reservations WHERE status = 'held'"
                     " ORDER BY rowid DESC LIMIT 15").fetchall()
    assert [(x["id"], x["rule_id"], x["level"]) for x in s["hold_queue"]] == \
        [(r["id"], r["rule_id"], r["level"]) for r in q]
    assert all(x["rule_id"] == "key_paused" and x["level"] == "hard" for x in s["hold_queue"])

    # Incident card.
    card = s["incident"]
    assert card["id"] == "inc_0001" and card["rule_id"] == "burst_count_5m"
    assert card["tripped_at"] == "2026-11-21 02:03:04" and card["status"] == "open"
    assert card["verdict"] is None and card["pr_url"] is None and card["pause_to_pr_min"] is None
    assert (card["held_count"], card["held_aed"]) == (36, 423000)

    # Layers: the latest real decision of each.
    l1 = s["layers"]["layer1"]
    assert l1["result"] == "paused" and l1["last"]["id"] == rows[0]["id"]
    assert l1["rules_loaded"] == 6
    l2 = s["layers"]["layer2"]
    assert l2["mode"] == "stub" and l2["t_history"] == 0.9 and l2["t_basis"] == 0.9
    last = conn.execute("SELECT * FROM jev_calls ORDER BY id DESC LIMIT 1").fetchone()
    assert l2["last"]["reservation"]["id"] == last["reservation_id"]
    assert l2["last"]["outcome"] == "kept" and l2["last"]["fits_history"] == 0.05
    assert l2["last"]["reservation"]["beneficiary_name"] == "Silverline Commodities"
    assert s["layers"]["layer3"] == {"incident_id": "inc_0001",
                                     "webhook": {"status": "not_configured", "text": None}}

    # Existing fields are still there.
    for name in ("keys", "reservations", "reservations_aed", "open_incident", "incidents", "events"):
        assert name in s
    conn.close()


def test_state_after_verdict_with_pr(stack):
    run(stack, "traffic_legit.py")
    run(stack, "traffic_attack.py")
    r = httpx.post(stack.breaker_url + "/incidents/inc_0001/verdict",
                   headers={"X-Admin-Token": "dev-admin-token"}, timeout=10,
                   json={"incident_id": "inc_0001", "verdict": "contact_client",
                         "reason": "Dormant beneficiaries, no bases.",
                         "pr_url": "https://github.com/example/repo/pull/7"})
    assert r.status_code == 200, r.text
    s = stack.get("/state")
    assert s["incident"]["verdict"] == "contact_client"
    assert s["incident"]["pr_url"] == "https://github.com/example/repo/pull/7"
    assert s["incident"]["pause_to_pr_min"] is not None
    # Without the soft case Jev was asked twice and released nothing.
    assert s["counters"]["released_by_jev"] == {"count": 0, "median_latency_ms": 0, "calls": 2,
                                                "answered": 2}
    assert s["counters"]["leaked"] == {"count": 6, "aed": 27000}


def test_jev_without_answer_shows_no_answer(make_stack):
    s = make_stack(env={"JEV_MODE": "live", "JEV_URL": "http://127.0.0.1:9/dead",
                        "TYPESAFE_API_KEY": "test-key"})
    run(s, "traffic_legit.py")
    run(s, "traffic_soft.py", "--expect", "held")
    st = s.get("/state")
    last = st["layers"]["layer2"]["last"]
    assert last["outcome"] == "no_answer" and last["fits_history"] is None
    assert st["counters"]["released_by_jev"]["median_latency_ms"] is None
    assert st["counters"]["stopped"] == {"count": 1, "aed": 48000}
    # A held payment with history and an approved matching basis is a legitimate hold.
    assert st["counters"]["legit_held"]["count"] == 1
