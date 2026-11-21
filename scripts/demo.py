"""Guided demo runner: drives the dashboard's presenter mode and runs the traffic scripts.

Beats: a tour of 7 stops, then for each of the 5 cases EXPLAIN (title card and captions)
and SHOW (real traffic, captions that follow real state changes in GET /state).
Between beats it waits for Enter; --auto advances on timers instead.

It talks only HTTP to the breaker (BREAKER_URL, ADMIN_TOKEN) and runs the existing scripts
as subprocesses. Every caption that states a result takes its numbers and names from
/state, so a caption can never contradict the screen.

Keys while the Devin beat waits: f then Enter applies the verdict of the rehearsal run
(.state/pr/rehearsal.json, else .state/pr/latest.json), s then Enter skips the wait.
After the last beat the runner stays up, so the services and the page stay as they are,
until q then Enter or Ctrl-C; only then does it clear the presenter state and exit.
Ctrl-C at any time clears the presenter state, stops the child processes and prints the
command that continues the demo on the same databases.
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

try:
    import termios
except ImportError:  # not on Windows; flush_input() is then a no-op
    termios = None

ROOT = Path(__file__).resolve().parent.parent
PYTHON = os.environ.get("PYTHON") or sys.executable
LATEST_PR = ROOT / ".state" / "pr" / "latest.json"
WATCHER = ROOT / "scripts" / "watch_pr.py"
LOG_DIR = Path(os.environ.get("DEMO_STATE_DIR") or ROOT / ".state" / "demo")
# The breaker's Jev budget per call: a failed call that took this long was a timeout.
JEV_TIMEOUT_S = float(os.environ.get("JEV_TIMEOUT_S", "1.0"))

CASES = {1: "Legit traffic", 2: "Soft stop", 3: "Attack", 4: "Devin", 5: "Replay"}
NUMBER_WORDS = ["Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
                "Ten"]
RULE_NAMES = {"burst_count_5m": "the burst rule", "burst_sum_1h": "the hourly sum rule",
              "drain_1h": "the account drain rule"}
VERDICT_WORDS = {"release": "release", "hold_until_morning": "hold until morning",
                 "contact_client": "contact the client",
                 "escalate_bank_security": "escalate to bank security"}
VERDICT_FIELDS = ("incident_id", "verdict", "reason", "evidence", "pr_url")
EVIDENCE_FIELDS = ("basis_ids", "prior_payment_ids")
WATCHER_EXIT = {
    0: "it applied the verdict of the pull request",
    2: "setup or authentication error, for example a bad GitHub token",
    3: "timeout, no usable verdict.json in time",
    4: "the breaker refused the verdict or could not be reached",
    5: "GitHub API error",
    130: "it was stopped",
}


def aed(x) -> str:
    x = x or 0
    return f"AED {int(x):,}" if float(x).is_integer() else f"AED {x:,.2f}"


def word(n: int) -> str:
    return NUMBER_WORDS[n] if 0 <= n < len(NUMBER_WORDS) else f"{n:,}"


def payments(n: int) -> str:
    return f"{word(n)} payment{'' if n == 1 else 's'}"


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


def flush_input(stream=None) -> None:
    """Discard terminal input typed before a prompt, so an early Enter never skips a beat.

    Does nothing when the stream is not a terminal (piped stdin in tests, --auto).
    """
    stream = sys.stdin if stream is None else stream
    if termios is None:
        return
    try:
        if stream.isatty():
            termios.tcflush(stream, termios.TCIFLUSH)
    except (OSError, ValueError, termios.error):
        pass


def watcher_exit_words(code: Optional[int]) -> str:
    if code is None:
        return "still running"
    if code < 0:
        return f"killed by signal {-code}"
    return WATCHER_EXIT.get(code, "unexpected error")


def jev_failure(j: dict) -> str:
    """'timeout' or 'error' for a Jev call without an answer, from its logged latency.

    The breaker gives a call JEV_TIMEOUT_S in total; a call that failed faster got an error
    (for example HTTP 401 or a refused connection), not a timeout.
    """
    lat = j.get("latency_ms")
    return "timeout" if lat is not None and lat >= JEV_TIMEOUT_S * 1000 * 0.95 else "error"


def jev_kept_text(j: Optional[dict], many: bool) -> Optional[str]:
    """Caption for a Jev call that gave no answer; None if Jev answered."""
    what = "the payments stay" if many else "the payment stays"
    if j is None:
        return f"Jev gave no answer, so {what} on hold."
    if j.get("outcome") == "no_answer":
        if jev_failure(j) == "timeout":
            return f"Jev did not answer in time, so {what} on hold."
        return f"Jev returned an error, so {what} on hold."
    return None


def webhook_caption(status: Optional[str], incident_id: str) -> str:
    if status == "sent":
        return f"Webhook sent. Devin starts on incident {incident_id}."
    if status == "failed":
        return f"The webhook to Devin failed for {incident_id}. The payments stay on hold."
    if status == "not_configured":
        return "Offline mode: no webhook is configured, so Devin is not called."
    return f"No webhook result for {incident_id} yet."


def webhook_short(status: Optional[str]) -> str:
    if status == "sent":
        return "The webhook to Devin has gone out."
    if status == "failed":
        return "The webhook to Devin failed."
    if status == "not_configured":
        return "Offline mode: no webhook to Devin."
    return "The webhook to Devin is on its way."


def skip_caption(status: Optional[str]) -> str:
    if status == "sent":
        return "We skip the wait. Devin keeps working in the background."
    if status == "failed":
        return "We skip the wait. The webhook failed, so Devin is not working on it."
    if status == "not_configured":
        return "We skip the wait. Offline mode: Devin was not called, so no pull request comes."
    return "We skip the wait. The webhook to Devin has no result yet."


def no_pr_caption(status: Optional[str]) -> str:
    """--auto: case 4 ends without a pull request."""
    if status == "sent":
        return "No pull request yet. Devin keeps working in the background."
    if status == "failed":
        return "No pull request: the webhook to Devin failed."
    if status == "not_configured":
        return "No pull request yet: offline mode."
    return "No pull request yet: the webhook has no result."


def verdict_caption(inc: dict) -> str:
    v = VERDICT_WORDS.get(inc["verdict"], inc["verdict"])
    req = inc.get("requested_verdict")
    held = inc.get("held_count") or 0
    if req == "release" and inc["verdict"] != "release":
        return (f"Verdict: release, changed to {v}: {payments(held).lower()} failed the "
                "re-check against the database.")
    if inc["verdict"] == "release":
        return ("Verdict: release. Every held payment passed the re-check against the "
                "database and was sent.")
    if held == 0:
        stay = "No payment is on hold."
    elif held == 1:
        stay = "One payment stays on hold."
    else:
        stay = f"{payments(held)} stay on hold."
    return f"Verdict: {v}. The breaker executes it. {stay}"


def pr_caption(inc: dict) -> str:
    pr = pr_short(inc["pr_url"])
    if inc.get("verdict_source") == "rehearsal":
        return (f"Pull request {pr} is from the rehearsal run, with a new SQL rule. "
                "A human reviews and merges it.")
    mins = inc.get("pause_to_pr_min")
    if mins is None:
        after = ""
    elif mins < 1:
        after = ", less than a minute after the pause"
    else:
        after = f", {mins:g} minute{'' if mins == 1 else 's'} after the pause"
    return (f"Devin opened pull request {pr}{after}, with a new SQL rule. "
            "A human reviews and merges it.")


def clean_verdict(data: dict) -> dict:
    """Only the fields the verdict endpoint accepts (it rejects unknown ones)."""
    body = {k: data[k] for k in VERDICT_FIELDS if k in data}
    if isinstance(body.get("evidence"), dict):
        body["evidence"] = {k: body["evidence"][k] for k in EVIDENCE_FIELDS
                            if k in body["evidence"]}
    return body


def read_pointer(path: Path) -> Optional[dict]:
    """A PR pointer file (.state/pr/latest.json or rehearsal.json), None if unusable."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


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
        self.before_rules: Optional[Path] = (Path(args.before_rules).resolve()
                                             if args.before_rules else None)
        # getattr: a test may build the runner from a partial namespace
        self.pr_state = Path(getattr(args, "pr_state", None) or LATEST_PR)
        self.rehearsal_state = Path(getattr(args, "rehearsal_state", None)
                                    or self.pr_state.parent / "rehearsal.json")
        log = getattr(args, "presenter_log", None)
        self.presenter_log = open(log, "a") if log else None
        self.t0 = time.monotonic()
        self.current = getattr(args, "from_case", 1)
        self.finished = False

    # --- HTTP -----------------------------------------------------------------

    def state(self) -> dict:
        r = self.http.get("/state")
        r.raise_for_status()
        return r.json()

    def present(self, **fields) -> None:
        r = self.http.post("/admin/presenter", json=fields, headers={"X-Admin-Token": self.token})
        if r.status_code != 200:
            raise RuntimeError(f"POST /admin/presenter: HTTP {r.status_code} {r.text}")
        if self.presenter_log:
            self.presenter_log.write(json.dumps(fields) + "\n")
            self.presenter_log.flush()

    def clear(self) -> None:
        try:
            self.http.delete("/admin/presenter", headers={"X-Admin-Token": self.token})
        except httpx.TransportError:
            pass  # the breaker is already gone, so there is nothing left to clear
        except httpx.HTTPError as e:
            print(f"could not clear the presenter state: {e}", file=sys.stderr)
        else:
            if self.presenter_log:
                self.presenter_log.write(json.dumps({"_clear": True}) + "\n")
        if self.presenter_log:
            self.presenter_log.close()
            self.presenter_log = None

    # --- timing ---------------------------------------------------------------

    def elapsed(self) -> str:
        el = int(time.monotonic() - self.t0)
        return f"{el // 60:02d}:{el % 60:02d}"

    def sleep(self, seconds: float) -> None:
        time.sleep(max(0.0, seconds * self.args.pace))

    def say(self, text: str, dwell: bool = True, **fields) -> None:
        """Show a caption (and other presenter fields), then leave it up for its reading time."""
        print(f"  > {text}", flush=True)
        self.present(caption=text, **fields)
        if dwell:
            self.sleep(reading_time(text))

    def prompt(self, text: str) -> str:
        """Read one line after discarding what was typed while the last step ran."""
        flush_input()
        return input(f"[{self.elapsed()}] {text} ")

    def beat(self, label: str) -> None:
        """Boundary between beats: Enter in manual mode, a short gap with --auto."""
        if self.args.auto:
            self.sleep(0.9)
            return
        try:
            self.prompt(f"[Enter] {label}")
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

    def wait_jev_call(self, jev0, timeout: float = 3.0) -> Optional[dict]:
        """Wait until /state shows a Jev call newer than jev0; None if none appears in time.

        The breaker commits the hold first and logs the Jev call afterwards, so a caption
        about Jev's answer must wait for the call to be visible.
        """
        s = self.wait_for(lambda x: (x["layers"]["layer2"]["last"] or {}).get("id") != jev0,
                          timeout)
        j = s["layers"]["layer2"]["last"] or {}
        return j if j.get("id") != jev0 else None

    # --- child processes ------------------------------------------------------

    def script(self, name: str, *args: str) -> subprocess.Popen:
        """Run scripts/<name> in its own process group, so only this runner stops it."""
        env = dict(os.environ, BREAKER_URL=self.url)
        p = subprocess.Popen([PYTHON, str(ROOT / "scripts" / name), *args], cwd=ROOT, env=env,
                             stdin=subprocess.DEVNULL, start_new_session=True)
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
            [PYTHON, str(watcher), "--since", since, "--timeout-min", "30",
             "--incident", inc["id"]], cwd=ROOT, env=env,
            stdout=self.watcher_log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True)
        print(f"  (PR watcher started, pid {self.watcher.pid}, since {since},"
              f" log {self.rel(LOG_DIR)}/watch_pr.log)")

    def watcher_exited(self) -> None:
        code = self.watcher.returncode
        print(f"  (PR watcher exited with code {code}: {watcher_exit_words(code)};"
              f" log {self.rel(LOG_DIR)}/watch_pr.log)")
        if self.watcher_log:
            self.watcher_log.close()
            self.watcher_log = None
        self.watcher = None

    @staticmethod
    def rel(path: Path) -> str:
        try:
            return path.resolve().relative_to(ROOT).as_posix()
        except ValueError:
            return str(path)

    # --- beats ----------------------------------------------------------------

    def tour(self) -> None:
        s = self.state()
        bank = s["bank_name"]
        client = (s.get("client") or {}).get("name") or "the client"
        stops = [
            ("all", f"This is TrustGap. It sits in front of the {bank} payments API "
                    "and limits money and behaviour, not requests."),
            ("layers", "Three layers: SQL rules in milliseconds, Jev by TypeSafe in under a "
                       "second, Devin with Graphify in minutes."),
            ("key", f"The state of the client's API key. Here: {client}'s payout key."),
            ("counters", "Money stopped. Money leaked. Legitimate payments held by mistake: "
                         "the target is zero."),
            ("stream", "Every payment request, live, newest on top."),
            ("queue", "Payments on hold. A hold is reversible, a release is not. So anything "
                      "unclear stays on hold."),
            ("incident", "When an API key is paused, an incident opens here. Devin's verdict "
                         "and pull request appear here too."),
        ]
        print("Tour")
        for i, (spot, text) in enumerate(stops, start=1):
            self.say(text, step=0, step_done=self.step_done, spotlight=spot, card=None,
                     panel=None)
            if i < len(stops):
                self.beat(f"next tour stop ({i + 1} of {len(stops)})")
        self.step_done.append(0)

    def explain(self, case: int, lines: list[str]) -> None:
        print(f"Case {case}: {CASES[case]}")
        self.present(step=case, step_done=self.step_done, spotlight=None, panel=None,
                     card={"kicker": f"Case {case}", "title": CASES[case]})
        for text in lines:
            self.say(text)
        self.beat(f"show case {case} live")
        self.present(card=None)

    def done(self, case: int) -> None:
        self.step_done.append(case)
        self.present(step_done=self.step_done, spotlight=None)

    def case1(self) -> None:
        s = self.state()
        client, bank = (s.get("client") or {}).get("name") or "The client", s["bank_name"]
        self.explain(1, [f"Case 1: a normal business day. {client} pays 42 supplier invoices "
                         f"through {bank}.",
                         "Watch: all 42 payments should go through, and none should be held."])
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
                text = ("Layer 1: a large amount at night. Soft stop: "
                        "the payment is on hold.")
            else:
                text = f"Layer 1: rule {row['rule_id']}. Soft stop: the payment is on hold."
        elif row.get("level") == "hard":
            text = f"Layer 1: hard stop by rule {row['rule_id']}. Jev is not asked."
        else:
            text = "Layer 1: no rule fires. The payment goes straight through."
        self.say(text, spotlight="layer1")
        if row.get("level") == "soft":
            s = self.wait_for(lambda x: (x["layers"]["layer2"]["last"] or {}).get("id") != jev0, 10)
            self.say("Layer 2: Jev reads the facts and answers two questions. Does it fit the "
                     "history? Does it match the invoice?", spotlight="layer2")
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pass
            j = self.wait_jev_call(jev0)
            l2 = self.state()["layers"]["layer2"]
            t = min(l2["t_history"], l2["t_basis"])
            kept = jev_kept_text(j, many=False)
            if kept:
                self.say(kept, spotlight="layer2")
            elif j.get("outcome") == "released":
                self.say(f"Both scores are above {t:.2f}, so the breaker releases the payment. "
                         "The threshold is in our code, not in Jev.", spotlight="layer2")
                lat = j.get("latency_ms") or 0
                first = "Released in under a second." if lat < 1000 else f"Released in {lat:,} ms."
                self.say(f"{first} Jev may only release a soft stop. "
                         "It can never override a hard stop.", spotlight="counters")
            else:
                self.say(f"Jev answered {j.get('fits_history', 0):.2f} and "
                         f"{j.get('purpose_matches_basis', 0):.2f}, below {t:.2f}, so the payment "
                         "stays on hold.", spotlight="layer2")
        p.wait()
        self.done(2)

    def case3(self) -> None:
        self.explain(3, ["Case 3: a stolen API key. At 02:00 the attacker pays three dormant "
                         "beneficiaries: approved long ago, never paid.",
                         "Watch: a few payments leak first. Then the breaker stops the rest."])
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
            self.say(f"{word(len(leaks))} payments of {aed(leaks[0]['amount_aed'])} pass, each "
                     "under every limit. No rule covers dormant beneficiaries yet."
                     if len({r["amount_aed"] for r in leaks}) == 1 else
                     f"{word(len(leaks))} small payments, {aed(sum(r['amount_aed'] for r in leaks))}"
                     " in total, pass, each under every limit.", spotlight="stream")
        soft = [r for r in rows if r["level"] == "soft"]
        if soft:
            j = self.wait_jev_call(jev0)
            s = self.state()
            jtext = jev_kept_text(j, many=True)
            if jtext is None:
                if j.get("outcome") == "kept":
                    jtext = "Jev says this does not fit the history, so the payments stay on hold."
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
            self.say(f"{who} trips {rule}. Hard stop. The API key is paused {secs} second"
                     f"{'' if secs == 1 else 's'} after the first violation.", spotlight="key")
            self.resume_traffic(p)
            # The rest of the attack streams in for a while: say what the page shows meanwhile.
            s = self.wait_for(lambda x: x["layers"]["layer3"]["webhook"]["status"] != "pending"
                              or p.poll() is not None, 4)
            if s["key"]["state"] == "paused":
                wh = s["layers"]["layer3"]["webhook"]["status"]
                self.say(f"Each further payment on the paused API key is held. "
                         f"{webhook_short(wh)}", spotlight="queue")
        p.wait()
        c = self.state()["counters"]
        self.say(f"Every later payment is held. {aed(c['stopped']['aed'])} stopped. "
                 f"But {aed(c['leaked']['aed'])} leaked.", spotlight="counters")
        self.done(3)

    def case4(self) -> None:
        self.explain(4, ["Case 4: closing the gap. The incident wakes Devin, a coding agent.",
                         "Devin never moves money. It investigates, gives a verdict and "
                         "proposes a new rule.",
                         "Devin also gets a Graphify code graph of this repo, to find every code "
                         "path that can send money."])
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
            self.say(verdict_caption(inc), spotlight="incident")
        if inc["pr_url"]:
            self.say(pr_caption(inc), spotlight="incident")
        self.done(4)

    def wait_devin(self, s: dict) -> None:
        inc = s["incident"]
        wh = s["layers"]["layer3"]["webhook"]["status"]
        if wh == "pending":
            s = self.wait_for(lambda x: x["layers"]["layer3"]["webhook"]["status"] != "pending", 5)
            wh = s["layers"]["layer3"]["webhook"]["status"]
        self.say(webhook_caption(wh, inc["id"]), spotlight="layer3")
        self.start_watcher(s)
        keys = "f + Enter: use the rehearsal pull request. s + Enter: skip the wait."
        if not self.args.auto:
            flush_input()
            print(f"[{self.elapsed()}] waiting for Devin. {keys}", flush=True)
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
            now_wh = s["layers"]["layer3"]["webhook"]["status"]
            if now_wh != wh:  # for example pending, then sent or failed
                wh = now_wh
                self.say(webhook_caption(wh, inc["id"]), dwell=False, spotlight="layer3")
                last_caption = time.monotonic()
            if self.watcher is not None and self.watcher.poll() is not None:
                self.watcher_exited()
                s = self.state()
                if s["incident"]["verdict"] and s["incident"]["pr_url"]:
                    return
            # Only a sent webhook means Devin is working; otherwise the caption above stays.
            if wh == "sent" and time.monotonic() - last_caption >= refresh:
                el = int(time.time() - t0)
                self.present(caption=f"Devin is working on {inc['id']}: {el // 60} min "
                                     f"{el % 60:02d} s since the pause.", spotlight="layer3")
                last_caption = time.monotonic()
            if self.args.auto:
                if self.watcher is None:
                    self.sleep(1.0)
                    self.say(no_pr_caption(wh), spotlight="layer3")
                    return
                time.sleep(1.0)
                continue
            ready, _, _ = select.select([sys.stdin], [], [], 1.0)
            if not ready:
                continue
            line = sys.stdin.readline()
            if not line:  # end of input: go on as in --auto
                self.args.auto = True
                continue
            key = line.strip().lower()
            if key == "s":
                self.say(skip_caption(wh), spotlight="layer3")
                return
            if key == "f" and self.fallback(inc["id"]):
                return
            flush_input()
            print(f"[{self.elapsed()}] {keys}", flush=True)

    def fallback(self, incident_id: str) -> bool:
        """Apply the verdict of the rehearsal run: rehearsal.json if it exists, else latest.json.

        The breaker gets the header X-Verdict-Source: rehearsal, so the page never shows the
        minutes from today's pause to a pull request that was opened in an earlier run.
        """
        path = self.rehearsal_state if self.rehearsal_state.is_file() else self.pr_state
        ptr = read_pointer(path)
        try:
            verdict = json.loads((ROOT / ptr["verdict_file"]).read_text())
            url = ptr["url"]
        except (OSError, ValueError, KeyError, TypeError) as e:
            print(f"  no rehearsal verdict available in {self.rel(path)}: {e!r}")
            return False
        if not isinstance(verdict, dict) or not isinstance(url, str) or not url:
            print(f"  no rehearsal verdict available in {self.rel(path)}")
            return False
        verdict = clean_verdict(verdict)
        verdict["incident_id"] = incident_id
        verdict["pr_url"] = url  # the pointer has the real URL, the file may not
        fd, tmp = tempfile.mkstemp(prefix="verdict.", suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(verdict, f)
            env = dict(os.environ, BREAKER_URL=self.url, ADMIN_TOKEN=self.token,
                       VERDICT_SOURCE="rehearsal")
            r = subprocess.run(["bash", str(ROOT / "scripts" / "apply_verdict.sh"), incident_id,
                                tmp], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=60)
        finally:
            os.unlink(tmp)
        if r.returncode != 0:
            print(f"  apply_verdict.sh failed: {r.stdout.strip()} {r.stderr.strip()}")
            return False
        self.say(f"This is the pull request from the rehearsal run: {pr_short(url)}. "
                 "Its verdict is applied now.", spotlight="layer3")
        return True

    def replay(self, rules_dir: Optional[Path]) -> tuple[Optional[dict], str]:
        """Run scripts/replay.sh; returns the parsed rows and the table text."""
        args = ["bash", str(ROOT / "scripts" / "replay.sh")]
        if rules_dir:
            args += ["--rules-dir", str(rules_dir)]
        p = subprocess.Popen(args, cwd=ROOT, env=dict(os.environ, PYTHON=PYTHON),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             stdin=subprocess.DEVNULL, start_new_session=True)
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

    def held_pr_rules(self, inc: Optional[dict]) -> Optional[Path]:
        """Rules folder of the pull request whose verdict the breaker holds, matched by URL."""
        url = (inc or {}).get("pr_url")
        if not url:
            return None
        for path in (self.rehearsal_state, self.pr_state):
            ptr = read_pointer(path)
            if not ptr or ptr.get("url") != url or not isinstance(ptr.get("rules_dir"), str):
                continue
            d = ROOT / ptr["rules_dir"]
            if d.is_dir() and any(d.glob("*.sql")):
                return d
        return None

    def case5(self) -> None:
        self.explain(5, ["Case 5: proof. We replay the same attack and the same normal day "
                         "against the new rule.",
                         "Watch the attack line go from FAIL to PASS. The normal day must pass "
                         "both times."])
        inc = self.state()["incident"]
        rules_dir = self.held_pr_rules(inc)
        title = "Replay on a fresh copy: the same attack and the same normal day"
        self.present(caption="First the replay with the current rules from the repository.",
                     spotlight="stream",
                     panel={"title": title,
                            "text": "Before: current rules from the repository\n\n"
                                    "running ..."})
        before, btext = self.replay(self.before_rules)
        text = "Before: current rules from the repository\n" + btext
        self.present(panel={"title": title, "text": text})
        if before:
            a = before["attack"]
            self.say(f"Before the new rule: {a['executed']} of {a['sent']} attack payments "
                     "executed.", spotlight="stream")
        if not rules_dir:
            if inc and inc.get("pr_url"):
                self.say(f"The rules of pull request {pr_short(inc['pr_url'])} are not on this "
                         "laptop, so there is no replay with them.", spotlight="stream")
            else:
                self.say("The fix is not available yet: there is no pull request with new rules "
                         "to replay against.", spotlight="stream")
            self.done(5)
            return
        rehearsal = inc.get("verdict_source") == "rehearsal"
        label = f"After: rules from pull request {pr_short(inc['pr_url'])}"
        if rehearsal:
            label += " (rehearsal run)"
        self.present(caption="Now the same replay with the rules from the "
                             f"{'rehearsal ' if rehearsal else ''}pull request.",
                     panel={"title": title, "text": text + "\n\n" + label + "\n\nrunning ..."})
        after, atext = self.replay(rules_dir)
        self.present(panel={"title": title, "text": text + "\n\n" + label + "\n" + atext})
        if after:
            a, lg = after["attack"], after["legit"]
            if a["executed"] == 0 and lg["executed"] == lg["sent"]:
                self.say(f"After the new rule: {a['executed']} of {a['sent']} attack payments "
                         f"executed. Legitimate payments: still {lg['executed']} of {lg['sent']}.",
                         spotlight="stream")
                leaked = self.state()["counters"]["leaked"]["aed"]
                self.say(f"The {aed(leaked)} leak is closed. {word(lg['held'])} legitimate "
                         "payments are held.", spotlight="stream")
            else:
                self.say(f"After the new rule: {a['executed']} of {a['sent']} attack payments "
                         f"executed, {lg['executed']} of {lg['sent']} legitimate. The fix does "
                         "not pass yet.",
                         spotlight="stream")
        self.done(5)

    def finish(self) -> None:
        """The demo is over: leave the page as it is and keep the services up until q."""
        self.finished = True
        print(f"[{self.elapsed()}] The demo is finished. The services keep running until "
              "q + Enter or Ctrl-C.", flush=True)
        if self.args.auto:
            return
        while True:
            try:
                line = self.prompt("[q + Enter] end the demo and stop the services")
            except EOFError:
                return
            if line.strip().lower() == "q":
                return

    def restart_command(self) -> str:
        """The command that continues the demo at the current case on the same databases."""
        extra = " --no-watcher" if self.args.no_watcher else ""
        if "DEMO_SH_FLAGS" in os.environ:
            flags = os.environ["DEMO_SH_FLAGS"].strip()
            flags = f" {flags}" if flags else ""
            return f"scripts/demo.sh --keep-data{flags}{extra} --from {self.current}"
        return f"scripts/demo.py --url {self.url}{extra} --from {self.current}"

    def run(self) -> int:
        self.state()  # fail early if the breaker is not reachable
        start = self.args.from_case
        self.step_done = list(range(0, start)) if start > 1 else []
        if start == 1 and not self.args.no_tour:
            self.present(card=None, panel=None, caption=None, spotlight=None, step=0,
                         step_done=[])
            self.beat("start the tour")
            self.tour()
            self.beat("explain case 1")
        cases = {1: self.case1, 2: self.case2, 3: self.case3, 4: self.case4, 5: self.case5}
        for case in range(start, 6):
            self.current = case
            if case == 4:
                self.start_watcher(self.state())
            cases[case]()
            if case < 5:
                self.beat(f"explain case {case + 1}")
        self.finish()
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
    ap.add_argument("--before-rules", metavar="DIR",
                    help="rules folder for the 'before' replay in case 5 (default: rules/)")
    ap.add_argument("--url", help="breaker URL (default: BREAKER_URL or http://127.0.0.1:8000)")
    ap.add_argument("--no-watcher", action="store_true", help="never start scripts/watch_pr.py")
    ap.add_argument("--watcher", default=str(WATCHER),
                    help="PR watcher script (default: scripts/watch_pr.py)")
    ap.add_argument("--pr-state", default=str(LATEST_PR),
                    help="PR watcher result file (default: .state/pr/latest.json)")
    ap.add_argument("--rehearsal-state", metavar="PATH",
                    help="pointer to the rehearsal pull request (default: rehearsal.json next "
                         "to --pr-state)")
    ap.add_argument("--presenter-log", metavar="PATH",
                    help="append every presenter update as one JSON line (for tests)")
    args = ap.parse_args(argv)

    runner = Runner(args)

    def on_term(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, on_term)
    try:
        return runner.run()
    except KeyboardInterrupt:
        if runner.finished:
            print("\nThe demo is finished.")
            return 0
        print(f"\ninterrupted. To continue on the same databases: {runner.restart_command()}")
        return 130
    except (httpx.HTTPError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        print(f"To continue on the same databases: {runner.restart_command()}", file=sys.stderr)
        return 1
    finally:
        runner.stop_children()
        runner.clear()
        runner.http.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
