"""Guided demo runner: drives the dashboard's presenter mode and runs the traffic scripts.

Beats: a tour of 7 stops, then for each of the 5 cases EXPLAIN (title card and captions)
and SHOW (real traffic, captions that follow real state changes in GET /state).
Between beats it waits for Enter; --auto advances on timers instead.

It talks only HTTP to the breaker (BREAKER_URL, ADMIN_TOKEN) and runs the existing scripts
as subprocesses. Every caption that states a result takes its numbers and names from
/state, so a caption can never contradict the screen.

Keys while the Devin beat waits: f then Enter applies the verdict of the rehearsal run
(.state/pr/latest.json), s then Enter skips the wait. Ctrl-C clears the presenter state
and stops the child processes.
"""

import argparse
import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import httpx

ROOT = Path(__file__).resolve().parent.parent
PYTHON = os.environ.get("PYTHON") or sys.executable
LATEST_PR = ROOT / ".state" / "pr" / "latest.json"
WATCHER = ROOT / "scripts" / "watch_pr.py"
LOG_DIR = ROOT / ".state" / "demo"

CASES = {1: "Legit traffic", 2: "Soft stop", 3: "Attack", 4: "Devin", 5: "Replay"}
NUMBER_WORDS = ["Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
                "Ten"]
RULE_NAMES = {"burst_count_5m": "the burst rule", "burst_sum_1h": "the hourly sum rule",
              "drain_1h": "the account drain rule"}
VERDICT_WORDS = {"release": "release", "hold_until_morning": "hold until morning",
                 "contact_client": "contact the client",
                 "escalate_bank_security": "escalate to bank security"}


def aed(x) -> str:
    x = x or 0
    return f"AED {int(x):,}" if float(x).is_integer() else f"AED {x:,.2f}"


def word(n: int) -> str:
    return NUMBER_WORDS[n] if 0 <= n < len(NUMBER_WORDS) else f"{n:,}"


def reading_time(text: str) -> float:
    """Seconds a caption stays up, the same formula as the design preview."""
    return max(2.5, len(text) * 0.06)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pr_short(url: Optional[str]) -> str:
    if not url:
        return "-"
    parts = url.rstrip("/").split("/")
    return f"#{parts[-1]}" if len(parts) >= 2 and parts[-2] == "pull" else url


class Runner:
    def __init__(self, args):
        self.args = args
        self.url = (args.url or os.environ.get("BREAKER_URL", "http://127.0.0.1:8000")).rstrip("/")
        self.token = os.environ.get("ADMIN_TOKEN", "dev-admin-token")
        self.http = httpx.Client(base_url=self.url, timeout=10.0)
        self.children: list[subprocess.Popen] = []
        self.watcher: Optional[subprocess.Popen] = None
        self.watcher_log = None
        self.step_done: list[int] = []
        self.paused_pids: set[int] = set()
        self.watcher_missing_noted = False

    # --- HTTP -----------------------------------------------------------------

    def state(self) -> dict:
        r = self.http.get("/state")
        r.raise_for_status()
        return r.json()

    def present(self, **fields) -> None:
        r = self.http.post("/admin/presenter", json=fields, headers={"X-Admin-Token": self.token})
        if r.status_code != 200:
            raise RuntimeError(f"POST /admin/presenter: HTTP {r.status_code} {r.text}")

    def clear(self) -> None:
        try:
            self.http.delete("/admin/presenter", headers={"X-Admin-Token": self.token})
        except httpx.HTTPError as e:
            print(f"could not clear the presenter state: {e}", file=sys.stderr)

    # --- timing ---------------------------------------------------------------

    def sleep(self, seconds: float) -> None:
        time.sleep(max(0.0, seconds * self.args.pace))

    def say(self, text: str, dwell: bool = True, **fields) -> None:
        """Show a caption (and other presenter fields), then leave it up for its reading time."""
        print(f"  > {text}")
        self.present(caption=text, **fields)
        if dwell:
            self.sleep(reading_time(text))

    def beat(self, label: str) -> None:
        """Boundary between beats: Enter in manual mode, a short gap with --auto."""
        if self.args.auto:
            self.sleep(0.9)
            return
        try:
            input(f"[Enter] {label} ")
        except EOFError:
            self.args.auto = True

    def wait_for(self, cond: Callable[[dict], bool], timeout: float, poll: float = 0.1) -> dict:
        """Poll /state until cond(state) is true or timeout seconds pass; returns the last state."""
        deadline = time.monotonic() + timeout
        s = self.state()
        while not cond(s) and time.monotonic() < deadline:
            time.sleep(poll)
            s = self.state()
        return s

    # --- child processes ------------------------------------------------------

    def script(self, name: str, *args: str) -> subprocess.Popen:
        """Run scripts/<name> in its own process group, so only this runner stops it."""
        env = dict(os.environ, BREAKER_URL=self.url)
        p = subprocess.Popen([PYTHON, str(ROOT / "scripts" / name), *args], cwd=ROOT, env=env,
                             start_new_session=True)
        self.children.append(p)
        return p

    def hold_traffic(self, p: subprocess.Popen) -> None:
        """Freeze a traffic script between requests, so the narration can catch up."""
        if self.args.no_hold or p.poll() is not None or not hasattr(signal, "SIGSTOP"):
            return
        os.killpg(p.pid, signal.SIGSTOP)
        self.paused_pids.add(p.pid)

    def resume_traffic(self, p: subprocess.Popen) -> None:
        if p.pid in self.paused_pids:
            self.paused_pids.discard(p.pid)
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGCONT)

    def stop_children(self) -> None:
        procs = list(self.children) + ([self.watcher] if self.watcher else [])
        for p in procs:
            if p.poll() is None:
                # Each child leads its own process group (replay.sh has a Python child too).
                if p.pid in self.paused_pids:
                    os.killpg(p.pid, signal.SIGCONT)
                os.killpg(p.pid, signal.SIGTERM)
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait()
        self.children = []
        self.watcher = None
        if self.watcher_log:
            self.watcher_log.close()
            self.watcher_log = None

    def start_watcher(self, s: dict) -> None:
        """Start scripts/watch_pr.py once, when an incident is open and has no PR yet."""
        inc = s.get("incident")
        if self.watcher is not None or not inc or inc["status"] != "open" or inc["pr_url"]:
            return
        if self.args.no_watcher:
            return
        watcher = Path(self.args.watcher)
        if not watcher.is_file():
            if not self.watcher_missing_noted:
                print(f"  ({watcher} does not exist, the PR watcher is skipped)")
                self.watcher_missing_noted = True
            return
        since = inc.get("tripped_wall_at") or utc_now()
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.watcher_log = open(LOG_DIR / "watch_pr.log", "w")
        env = dict(os.environ, BREAKER_URL=self.url, ADMIN_TOKEN=self.token)
        self.watcher = subprocess.Popen(
            [PYTHON, str(watcher), "--since", since, "--timeout-min", "30"], cwd=ROOT, env=env,
            stdout=self.watcher_log, stderr=subprocess.STDOUT, start_new_session=True)
        print(f"  (PR watcher started, pid {self.watcher.pid}, since {since},"
              f" log {LOG_DIR.relative_to(ROOT)}/watch_pr.log)")

    # --- beats ----------------------------------------------------------------

    def tour(self) -> None:
        s = self.state()
        bank = s["bank_name"]
        client = (s.get("client") or {}).get("name") or "the client"
        stops = [
            ("all", f"This is Night Circuit Breaker. It sits in front of the {bank} payments API "
                    "and limits money and behaviour, not request counts."),
            ("layers", "Three layers decide. Layer 1: SQL rules, in milliseconds. Layer 2: Jev, "
                       "a decision model, in under a second. Layer 3: Devin, a coding agent, "
                       "in minutes."),
            ("key", f"The state of one client API key. Here: {client}'s payout key."),
            ("counters", "Money stopped. Money leaked. Legitimate payments held by mistake, "
                         "target zero. Payments released by Jev. Time from first violation to "
                         "pause."),
            ("stream", "Every payout request, as it arrives."),
            ("queue", "Payments on hold. A hold is reversible, a release is not. So anything "
                      "unclear stays on hold."),
            ("incident", "When a key is paused, an incident opens. The verdict and the pull "
                         "request from Devin appear here."),
        ]
        print("Tour")
        for i, (spot, text) in enumerate(stops, start=1):
            self.say(text, step=0, step_done=self.step_done, spotlight=spot, card=None,
                     panel=None)
            if i < len(stops):
                self.beat(f"tour stop {i + 1} of {len(stops)}")
        self.step_done.append(0)

    def explain(self, case: int, lines: list[str]) -> None:
        print(f"Case {case}: {CASES[case]}")
        self.present(step=case, step_done=self.step_done, spotlight=None, panel=None,
                     card={"kicker": f"Case {case}", "title": CASES[case]})
        for text in lines:
            self.say(text)
        self.beat(f"show case {case}")
        self.present(card=None)

    def done(self, case: int) -> None:
        self.step_done.append(case)
        self.present(step_done=self.step_done, spotlight=None)

    def case1(self) -> None:
        s = self.state()
        client, bank = (s.get("client") or {}).get("name") or "The client", s["bank_name"]
        self.explain(1, [f"Case 1: a normal business day. {client} pays 42 supplier invoices "
                         f"through {bank}.", "Nothing should be stopped."])
        before = s["reservations"]
        p = self.script("traffic_legit.py", "--delay", str(self.args.legit_delay),
                        "--trace", str(ROOT / "traces" / "demo_legit.jsonl"))
        self.say("Layer 1 checks each payment against the client's own history: usual time, "
                 "usual amount, known beneficiary.", spotlight="layer1")
        self.present(spotlight="stream")
        p.wait()
        after = self.state()["reservations"]
        delta = {k: after[k] - before.get(k, 0) for k in after}
        sent, ex, held = sum(delta.values()), delta["executed"], delta["held"]
        if ex == sent and held == 0:
            text = (f"{ex} of {sent} executed. {word(held)} held. "
                    "For normal business the breaker is invisible.")
        else:
            text = f"{ex} of {sent} executed, {held} held."
        self.say(text, spotlight="counters")
        self.done(1)

    def case2(self) -> None:
        self.explain(2, ["Case 2: unusual, but legitimate. 02:10 at night, AED 48,000 to a "
                         "known supplier, with an approved invoice.",
                         "Watch Layer 1 hold it and Layer 2 release it."])
        s0 = self.state()
        n0 = sum(s0["reservations"].values())
        jev0 = (s0["layers"]["layer2"]["last"] or {}).get("id")
        p = self.script("traffic_soft.py", "--delay", "0", "--expect", "executed",
                        "--trace", str(ROOT / "traces" / "demo_soft.jsonl"))
        s = self.wait_for(lambda x: sum(x["reservations"].values()) > n0, 15)
        row = s["stream"][0] if s["stream"] else {}
        if row.get("level") == "soft":
            if row.get("rule_id") == "off_hours_amount":
                text = ("Layer 1: off hours and a large amount. Soft stop. "
                        "The payment is on hold.")
            else:
                text = f"Layer 1: {row['rule_id']}. Soft stop. The payment is on hold."
        elif row.get("level") == "hard":
            text = f"Layer 1: hard stop by {row['rule_id']}. Jev is not asked."
        else:
            text = "Layer 1: no rule fires, the payment goes straight through."
        self.say(text, spotlight="layer1")
        if row.get("level") == "soft":
            s = self.wait_for(lambda x: (x["layers"]["layer2"]["last"] or {}).get("id") != jev0, 10)
            self.say("Layer 2: Jev gets aggregated facts and answers two questions with "
                     "probabilities.", spotlight="layer2")
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pass
            l2 = self.state()["layers"]["layer2"]
            j = l2["last"] or {}
            t = min(l2["t_history"], l2["t_basis"])
            if j.get("outcome") == "released":
                self.say(f"Both are above {t:.2f}, so the breaker releases the payment. "
                         "The thresholds live in our code, not in the model.", spotlight="layer2")
                lat = j.get("latency_ms") or 0
                first = "Released in under a second." if lat < 1000 else f"Released in {lat:,} ms."
                self.say(f"{first} Jev may only release a soft stop. "
                         "It can never override a hard stop.", spotlight="counters")
            elif j.get("outcome") == "no_answer":
                self.say("No answer in time, the payment stays on hold: fail closed.",
                         spotlight="layer2")
            else:
                self.say(f"Jev answered {j.get('fits_history', 0):.2f} and "
                         f"{j.get('purpose_matches_basis', 0):.2f}, below {t:.2f}, so the payment "
                         "stays on hold.", spotlight="layer2")
        p.wait()
        self.done(2)

    def case3(self) -> None:
        self.explain(3, ["Case 3: a stolen API key. At 02:00 an attacker starts paying three "
                         "beneficiaries that were approved long ago and never paid.",
                         "Watch the leak, then the stop."])
        s0 = self.state()
        last_id = s0["stream"][0]["id"] if s0["stream"] else ""
        jev0 = (s0["layers"]["layer2"]["last"] or {}).get("id")
        p = self.script("traffic_attack.py", "--delay", str(self.args.attack_delay),
                        "--trace", str(ROOT / "traces" / "demo_attack.jsonl"))

        def new_rows(x):
            return [r for r in x["stream"] if r["id"] > last_id]

        self.present(spotlight="stream", caption=None)
        s = self.wait_for(lambda x: any(r["level"] or r["status"] == "held" for r in new_rows(x))
                          or p.poll() is not None, 60)
        self.hold_traffic(p)
        s = self.state()
        rows = new_rows(s)
        leaks = [r for r in rows if r["status"] == "executed"]
        if leaks:
            self.say(f"{word(len(leaks))} small payments, {aed(leaks[0]['amount_aed'])} each, "
                     "below every threshold. They pass. No rule covers dormant beneficiaries yet."
                     if len({r["amount_aed"] for r in leaks}) == 1 else
                     f"{word(len(leaks))} small payments, {aed(sum(r['amount_aed'] for r in leaks))}"
                     " in total, below every threshold. They pass.", spotlight="stream")
        soft = [r for r in rows if r["level"] == "soft"]
        if soft:
            j = s["layers"]["layer2"]["last"] or {}
            if j.get("id") == jev0:
                jtext = "Jev was not asked."
            elif j.get("outcome") == "kept":
                jtext = "Jev says this does not fit the history. The payments stay on hold."
            elif j.get("outcome") == "no_answer":
                jtext = "Jev gave no answer in time. The payments stay on hold: fail closed."
            else:
                jtext = "Jev released it."
            self.say(f"The burst starts. Soft stop. {jtext}", spotlight="layer2")
        self.resume_traffic(p)
        s = self.wait_for(lambda x: x["key"]["state"] == "paused" or p.poll() is not None, 60)
        if s["key"]["state"] == "paused":
            self.hold_traffic(p)
            s = self.state()
            self.start_watcher(s)
            inc = s["incident"] or {}
            rows = sorted(new_rows(s), key=lambda r: r["id"])
            trip = next((i for i, r in enumerate(rows, start=1)
                         if r["level"] == "hard" and r["rule_id"] == inc.get("rule_id")), None)
            rule = RULE_NAMES.get(inc.get("rule_id"), f"rule {inc.get('rule_id')}")
            secs = round((s["counters"]["violation_to_pause_ms"] or 0) / 1000)
            who = f"Payment {trip}" if trip else "A payment"
            self.say(f"{who} trips {rule}. Hard stop. The key is paused {secs} second"
                     f"{'' if secs == 1 else 's'} after the first violation.", spotlight="key")
            self.resume_traffic(p)
        p.wait()
        c = self.state()["counters"]
        self.say(f"Every further payment is held. {aed(c['stopped']['aed'])} stopped. "
                 f"{aed(c['leaked']['aed'])} leaked.", spotlight="counters")
        self.done(3)

    def case4(self) -> None:
        self.explain(4, ["Case 4: closing the gap. The incident wakes Devin, a coding agent.",
                         "Devin never moves money. It investigates and proposes."])
        s = self.state()
        inc = s["incident"]
        if not inc:
            self.say("No incident is open, so Devin has nothing to do.", spotlight="layer3")
            self.done(4)
            return
        if not (inc["verdict"] and inc["pr_url"]):
            self.wait_devin(s)
        s = self.state()
        inc = s["incident"]
        if inc["verdict"]:
            v = VERDICT_WORDS.get(inc["verdict"], inc["verdict"])
            req = inc.get("requested_verdict")
            if req and req != inc["verdict"]:
                v = f"{VERDICT_WORDS.get(req, req)}, downgraded to {v} by the re-check"
            self.say(f"Verdict: {v}. The breaker executes the verdict and would re-check any "
                     "release against the database.", spotlight="incident")
        if inc["pr_url"]:
            mins = inc.get("pause_to_pr_min")
            if mins is None:
                after = ""
            elif mins < 1:
                after = ", less than a minute after the pause"
            else:
                after = f", {mins:g} minute{'' if mins == 1 else 's'} after the pause"
            self.say(f"Devin opened pull request {pr_short(inc['pr_url'])}{after}, with a new SQL "
                     "rule. A human reviews and merges it.", spotlight="incident")
        self.done(4)

    def wait_devin(self, s: dict) -> None:
        inc = s["incident"]
        wh = s["layers"]["layer3"]["webhook"]
        if wh["status"] == "pending":
            s = self.wait_for(lambda x: x["layers"]["layer3"]["webhook"]["status"] != "pending", 5)
            wh = s["layers"]["layer3"]["webhook"]
        if wh["status"] == "sent":
            self.say(f"Webhook sent to Devin for {inc['id']}.", spotlight="layer3")
        elif wh["status"] == "failed":
            self.say(f"The webhook to Devin failed for {inc['id']}. The holds stay.",
                     spotlight="layer3")
        elif wh["status"] == "not_configured":
            self.say("No webhook configured, offline mode.", spotlight="layer3")
        else:
            self.say(f"No webhook outcome logged for {inc['id']} yet.", spotlight="layer3")
        self.start_watcher(s)
        if not self.args.auto:
            print("  waiting for Devin: f + Enter = use the rehearsal PR, s + Enter = skip")
        started = inc.get("tripped_wall_at")
        t0 = time.time()
        try:
            t0 = datetime.fromisoformat(started).timestamp() if started else t0
        except ValueError:
            pass
        last_caption = 0.0
        refresh = max(0.2, 5 * self.args.pace)
        while True:
            s = self.state()
            inc = s["incident"]
            if inc and inc["verdict"] and inc["pr_url"]:
                return
            if self.watcher is not None and self.watcher.poll() is not None:
                print(f"  (PR watcher exited with code {self.watcher.returncode})")
                if self.watcher_log:
                    self.watcher_log.close()
                    self.watcher_log = None
                self.watcher = None
                s = self.state()
                if s["incident"]["verdict"] and s["incident"]["pr_url"]:
                    return
            if time.monotonic() - last_caption >= refresh:
                el = int(time.time() - t0)
                self.present(caption=f"Devin is working on {inc['id']}: {el // 60} min "
                                     f"{el % 60:02d} s since the pause.", spotlight="layer3")
                last_caption = time.monotonic()
            if self.args.auto:
                if self.watcher is None:
                    reason = ("no webhook configured, offline mode"
                              if wh["status"] == "not_configured" else "no PR watcher running")
                    self.sleep(1.0)
                    self.say(f"No pull request yet: {reason}.", spotlight="layer3")
                    return
                time.sleep(1.0)
                continue
            ready, _, _ = select.select([sys.stdin], [], [], 1.0)
            if not ready:
                continue
            key = sys.stdin.readline().strip().lower()
            if key == "s":
                self.say("We skip the wait. Devin keeps working in the background.",
                         spotlight="layer3")
                return
            if key == "f" and self.fallback(inc["id"]):
                return
            print("  f + Enter = use the rehearsal PR, s + Enter = skip")

    def fallback(self, incident_id: str) -> bool:
        """Apply the verdict of the rehearsal run named in .state/pr/latest.json."""
        try:
            latest = json.loads(Path(self.args.pr_state).read_text())
            verdict = json.loads((ROOT / latest["verdict_file"]).read_text())
        except (OSError, ValueError, KeyError) as e:
            print(f"  no rehearsal verdict available: {e}")
            return False
        verdict["incident_id"] = incident_id
        verdict.setdefault("pr_url", latest.get("url"))
        if not verdict.get("pr_url"):
            verdict["pr_url"] = latest.get("url")
        self.say(f"This is the pull request from the rehearsal run: {pr_short(latest.get('url'))}."
                 " We apply its verdict now.", spotlight="layer3")
        fd, path = tempfile.mkstemp(prefix="verdict.", suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(verdict, f)
            env = dict(os.environ, BREAKER_URL=self.url, ADMIN_TOKEN=self.token)
            r = subprocess.run(["bash", str(ROOT / "scripts" / "apply_verdict.sh"), incident_id,
                                path], cwd=ROOT, env=env, capture_output=True, text=True,
                               timeout=60)
        finally:
            os.unlink(path)
        if r.returncode != 0:
            print(f"  apply_verdict.sh failed: {r.stdout.strip()} {r.stderr.strip()}")
            return False
        return True

    def replay(self, rules_dir: Optional[Path]) -> tuple[Optional[dict], str]:
        """Run scripts/replay.sh; returns the parsed rows and the table text."""
        args = ["bash", str(ROOT / "scripts" / "replay.sh")]
        if rules_dir:
            args += ["--rules-dir", str(rules_dir)]
        p = subprocess.Popen(args, cwd=ROOT, env=dict(os.environ, PYTHON=PYTHON),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             start_new_session=True)
        self.children.append(p)
        out, _ = p.communicate(timeout=600)
        self.children.remove(p)
        lines = out.splitlines()
        rows, table = {}, []
        for i, line in enumerate(lines):
            if line.strip().startswith("sent") and "executed" in line:
                table = lines[i:i + 3]
        for line in table[1:]:
            parts = line.split()
            if len(parts) >= 5 and parts[0] in ("attack", "legit"):
                rows[parts[0]] = {"sent": int(parts[1]), "executed": int(parts[2]),
                                  "held": int(parts[3]), "result": parts[4]}
        if len(rows) != 2:
            print(f"  replay output not understood:\n{out}")
            return None, out.strip()[-600:]
        return rows, "\n".join(table)

    def case5(self) -> None:
        self.explain(5, ["Case 5: proof. We replay the same attack and the same normal day "
                         "against the new rule."])
        rules_dir, pr = None, None
        try:
            pr = json.loads(Path(self.args.pr_state).read_text())
            d = ROOT / pr["rules_dir"]
            rules_dir = d if any(d.glob("*.sql")) else None
        except (OSError, ValueError, KeyError, TypeError):
            pr = None
        title = "Replay: the same attack and the same normal day on a fresh instance"
        self.present(caption="Replaying the attack against the rules in the repository.",
                     spotlight="stream",
                     panel={"title": title, "text": "Before, rules in the repository:\n\n"
                                                    "running ..."})
        before, btext = self.replay(None)
        text = "Before, rules in the repository:\n" + btext
        self.present(panel={"title": title, "text": text})
        if before:
            a = before["attack"]
            self.say(f"Before the new rule: {a['executed']} of {a['sent']} attack payments were "
                     "executed.", spotlight="stream")
        if not rules_dir:
            self.say("The fix is not available yet: there is no pull request with new rules "
                     "to replay against.", spotlight="stream")
            self.done(5)
            return
        label = f"After, rules from pull request {pr_short(pr.get('url'))}:"
        self.present(caption="Now the same replay against the rules from the pull request.",
                     panel={"title": title, "text": text + "\n\n" + label + "\n\nrunning ..."})
        after, atext = self.replay(rules_dir)
        self.present(panel={"title": title, "text": text + "\n\n" + label + "\n" + atext})
        if after:
            a, lg = after["attack"], after["legit"]
            if a["executed"] == 0 and lg["executed"] == lg["sent"]:
                self.say(f"After: {a['executed']} of {a['sent']}. And legit traffic is still "
                         f"{lg['executed']} of {lg['sent']}.", spotlight="stream")
                leaked = self.state()["counters"]["leaked"]["aed"]
                self.say(f"{aed(leaked)} leak closed. {word(lg['held'])} legitimate payments "
                         "held.", spotlight="stream")
            else:
                self.say(f"After: {a['executed']} of {a['sent']} attack payments executed, "
                         f"{lg['executed']} of {lg['sent']} legit. The fix does not pass yet.",
                         spotlight="stream")
        self.done(5)

    def run(self) -> int:
        self.state()  # fail early if the breaker is not reachable
        start = self.args.from_case
        self.step_done = list(range(0, start)) if start > 1 else []
        if start == 1 and not self.args.no_tour:
            self.present(card=None, panel=None, caption=None, spotlight=None, step=0,
                         step_done=[])
            self.beat("start the tour")
            self.tour()
            self.beat("case 1")
        cases = {1: self.case1, 2: self.case2, 3: self.case3, 4: self.case4, 5: self.case5}
        for case in range(start, 6):
            if case == 4:
                self.start_watcher(self.state())
            cases[case]()
            if case < 5:
                self.beat(f"case {case + 1}")
        self.present(spotlight=None)
        self.beat("end the demo (clears the subtitles and the spotlight)")
        return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="scripts/demo.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--auto", action="store_true", help="advance on timers, do not wait for Enter")
    ap.add_argument("--from", dest="from_case", type=int, default=1, choices=range(1, 6),
                    metavar="N", help="start at case N (1-5) with the current system state")
    ap.add_argument("--no-tour", action="store_true", help="skip the tour")
    ap.add_argument("--pace", type=float, default=1.0,
                    help="multiplier for caption reading times and gaps (default 1)")
    ap.add_argument("--legit-delay", type=float, default=0.12, help="seconds between legit payments")
    ap.add_argument("--attack-delay", type=float, default=0.25, help="seconds between attack payments")
    ap.add_argument("--no-hold", action="store_true",
                    help="never pause the attack script while a caption is read")
    ap.add_argument("--url", help="breaker URL (default: BREAKER_URL or http://127.0.0.1:8000)")
    ap.add_argument("--no-watcher", action="store_true", help="never start scripts/watch_pr.py")
    ap.add_argument("--watcher", default=str(WATCHER),
                    help="PR watcher script (default: scripts/watch_pr.py)")
    ap.add_argument("--pr-state", default=str(LATEST_PR),
                    help="PR watcher result file (default: .state/pr/latest.json)")
    args = ap.parse_args(argv)

    runner = Runner(args)

    def on_term(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, on_term)
    try:
        return runner.run()
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130
    except (httpx.HTTPError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        runner.stop_children()
        runner.clear()
        runner.http.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
