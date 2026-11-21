"""Stage rehearsal fixes: rehearsal verdicts, truthful captions, the terminal, demo.sh.

The guided runs use a pseudo terminal, like a presenter at a keyboard. Everything runs in
stub mode with local fakes and free ports.
"""

import json
import os
import pty
import re
import select
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import demo
import replaylib
from conftest import PYTHON, ROOT, baseline_rules_dir
from test_webhook import Receiver, run_demo, wait_for

ADMIN = {"X-Admin-Token": "dev-admin-token"}
INCIDENT_TRACE = replaylib.INCIDENTS_DIR / "inc_0001.jsonl"
REHEARSAL_URL = "https://github.com/acme/demo/pull/9"
OTHER_URL = "https://github.com/acme/demo/pull/12"


def verdict_body(**over) -> dict:
    body = {"incident_id": "inc_0001", "verdict": "contact_client",
            "reason": "Synthetic test verdict.", "pr_url": REHEARSAL_URL}
    body.update(over)
    return body


def post_verdict(stack, body, **headers):
    return httpx.post(f"{stack.breaker_url}/incidents/inc_0001/verdict", json=body,
                      headers={**ADMIN, **headers}, timeout=30)


def attack(stack):
    legit = replaylib.replay(stack.breaker_url, replaylib.LEGIT_TRACE)
    att = replaylib.replay(stack.breaker_url, INCIDENT_TRACE)
    assert (legit.executed, att.executed, att.held) == (42, 6, 36)


def port_free(url: str) -> bool:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", int(url.rsplit(":", 1)[1])))
        except OSError:
            return False
    return True


def remove_runner_traces():
    for name in ("legit", "soft", "attack"):
        (ROOT / "traces" / f"demo_{name}.jsonl").unlink(missing_ok=True)


# --- A: the breaker knows a verdict came from the rehearsal -------------------------

def test_rehearsal_verdict_has_no_pause_to_pr_minutes(stack):
    attack(stack)
    assert post_verdict(stack, verdict_body(), **{"X-Verdict-Source": "rehearsal"}).status_code == 200
    s = stack.get("/state")
    assert s["incident"]["verdict_source"] == "rehearsal"
    assert s["incident"]["pr_url"] == REHEARSAL_URL
    assert s["incident"]["pause_to_pr_min"] is None
    assert s["incidents"][0]["verdict_source"] == "rehearsal"
    assert s["incidents"][0]["pause_to_pr_min"] is None

    # Only the admin may send it, and only known values; the body stays strict.
    r = httpx.post(f"{stack.breaker_url}/incidents/inc_0001/verdict", json=verdict_body(),
                   headers={"X-Verdict-Source": "rehearsal"}, timeout=30)
    assert r.status_code == 401
    assert post_verdict(stack, verdict_body(), **{"X-Verdict-Source": "other"}).status_code == 422
    assert post_verdict(stack, verdict_body(source="rehearsal")).status_code == 422

    # A live verdict (no header) gets its minutes again.
    assert post_verdict(stack, verdict_body(pr_url=OTHER_URL)).status_code == 200
    inc = stack.get("/state")["incident"]
    assert inc["verdict_source"] == "live" and inc["pause_to_pr_min"] is not None


def test_apply_verdict_sh_sends_the_rehearsal_header(stack, tmp_path):
    attack(stack)
    f = tmp_path / "verdict.json"
    f.write_text(json.dumps(verdict_body()))
    env = dict(stack.env, BREAKER_URL=stack.breaker_url, ADMIN_TOKEN="dev-admin-token")
    bad = subprocess.run(["bash", str(ROOT / "scripts" / "apply_verdict.sh"), "inc_0001", str(f)],
                         env=dict(env, VERDICT_SOURCE="bogus"), capture_output=True, text=True)
    assert bad.returncode == 2
    r = subprocess.run(["bash", str(ROOT / "scripts" / "apply_verdict.sh"), "inc_0001", str(f)],
                       env=dict(env, VERDICT_SOURCE="rehearsal"), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert stack.get("/state")["incident"]["verdict_source"] == "rehearsal"


def test_dashboard_says_from_the_rehearsal_run():
    page = (ROOT / "breaker" / "static" / "index.html").read_text()
    # incident card and Layer 3 checklist
    assert page.count("'from the rehearsal run'") == 2


# --- J and K: the webhook body --------------------------------------------------------

def test_webhook_names_the_public_trace_path_and_the_jev_model(make_stack):
    receiver = Receiver()
    committed = INCIDENT_TRACE.read_bytes()
    try:
        stack = make_stack(env={"DEVIN_WEBHOOK_URL": receiver.url,
                                "TRACE_FILE_PUBLIC_DIR": "fixtures/incidents",
                                "JEV_MODEL": "jev-9.9.9"})
        run_demo(stack)
        assert wait_for(lambda: receiver.calls)
        body = json.loads(receiver.calls[0][1])
    finally:
        receiver.close()
    assert body["trace_file"] == "fixtures/incidents/inc_0001.jsonl"
    assert body["jev"]["model"] == "jev-9.9.9"  # the same JEV_MODEL that breaker/jev.py uses
    assert (stack.workdir / "incidents" / "inc_0001.jsonl").is_file()
    assert INCIDENT_TRACE.read_bytes() == committed


# --- B and M: captions and terminal lines ---------------------------------------------

def test_captions_follow_the_state():
    assert "did not answer in time" in demo.jev_kept_text(
        {"outcome": "no_answer", "latency_ms": 1004}, many=False)
    assert "returned an error" in demo.jev_kept_text(
        {"outcome": "no_answer", "latency_ms": 38}, many=True)
    assert demo.jev_kept_text({"outcome": "kept", "latency_ms": 250}, many=True) is None
    assert "gave no answer" in demo.jev_kept_text(None, many=False)

    for status in ("failed", "not_configured", "pending", None):
        for text in (demo.webhook_caption(status, "inc_0001"), demo.skip_caption(status),
                     demo.no_pr_caption(status), demo.webhook_short(status)):
            assert "working" not in text or "not working" in text, text
    assert "keeps working" in demo.skip_caption("sent")
    assert "Offline mode" in demo.skip_caption("not_configured")
    assert "failed" in demo.skip_caption("failed")

    down = demo.verdict_caption({"verdict": "contact_client", "requested_verdict": "release",
                                 "held_count": 36})
    assert down == ("Verdict: release, changed to contact the client: 36 payments failed the "
                    "re-check against the database.")
    assert "would" not in down
    kept = demo.verdict_caption({"verdict": "contact_client", "requested_verdict": "contact_client",
                                 "held_count": 36})
    assert kept == "Verdict: contact the client. The breaker executes it. 36 payments stay on hold."
    rel = demo.verdict_caption({"verdict": "release", "requested_verdict": "release",
                                "held_count": 0})
    assert "passed the re-check" in rel

    reh = demo.pr_caption({"pr_url": REHEARSAL_URL, "verdict_source": "rehearsal",
                           "pause_to_pr_min": None})
    assert "#9 is from the rehearsal run" in reh and "minute" not in reh
    live = demo.pr_caption({"pr_url": REHEARSAL_URL, "verdict_source": "live",
                            "pause_to_pr_min": 4.0})
    assert "4 minutes after the pause" in live

    assert "authentication" in demo.watcher_exit_words(2)
    assert "timeout" in demo.watcher_exit_words(3)
    assert "breaker" in demo.watcher_exit_words(4)
    assert "GitHub" in demo.watcher_exit_words(5)
    assert demo.watcher_exit_words(-9) == "killed by signal 9"

    every = [demo.webhook_caption(s, "inc_0001") for s in ("sent", "failed", "not_configured", None)]
    every += [f(s) for f in (demo.skip_caption, demo.no_pr_caption) for s in
              ("sent", "failed", "not_configured", None)]
    every += ["Each further payment on the paused API key is held. " + demo.webhook_short(s)
              for s in ("sent", "failed", "not_configured", None)]
    every += [down, kept, rel, reh, live]
    for text in every:
        assert len(text) <= 110, text
        assert "\u2014" not in text


def test_flush_input_discards_typed_lines_on_a_terminal():
    master, slave = pty.openpty()
    try:
        os.write(master, b"\n")
        f = os.fdopen(slave, "rb", buffering=0)
        try:
            assert select.select([f], [], [], 1.0)[0]
            demo.flush_input(f)
            assert not select.select([f], [], [], 0.2)[0]
        finally:
            f.close()
    finally:
        os.close(master)
    r, w = os.pipe()
    with os.fdopen(r, "rb") as pr, os.fdopen(w, "wb"):
        demo.flush_input(pr)  # not a terminal: nothing happens


def test_clear_is_silent_when_the_breaker_is_gone(tmp_path, capsys):
    args = SimpleNamespace(url=f"http://127.0.0.1:{replaylib.free_port()}", before_rules=None,
                           pr_state=str(tmp_path / "latest.json"), rehearsal_state=None,
                           presenter_log=None, from_case=1)
    runner = demo.Runner(args)
    runner.clear()
    runner.http.close()
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


# --- the guided runner in a pseudo terminal ---------------------------------------------

class Terminal:
    """Runs a command with a pseudo terminal as stdin, stdout and stderr."""

    def __init__(self, argv, env):
        self.master, slave = pty.openpty()
        self.proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, cwd=ROOT,
                                     env=env, start_new_session=True)
        os.close(slave)
        self.out = ""
        self.eof = False

    def pump(self, timeout: float) -> None:
        if self.eof:
            time.sleep(timeout)
            return
        if select.select([self.master], [], [], timeout)[0]:
            try:
                data = os.read(self.master, 65536)
            except OSError:  # EIO: the child closed the terminal
                data = b""
            if not data:
                self.eof = True
            self.out += data.decode(errors="replace").replace("\r\n", "\n")

    def expect(self, pattern: str, start: int = 0, timeout: float = 120) -> int:
        """Wait until pattern (a regex) shows up after `start`; returns the end of the match."""
        deadline = time.monotonic() + timeout
        while True:
            m = re.search(pattern, self.out[start:])
            if m:
                return start + m.end()
            if time.monotonic() > deadline or (self.eof and self.proc.poll() is not None):
                raise AssertionError(f"{pattern!r} not seen (exit code {self.proc.poll()})."
                                     f" Output:\n{self.out}")
            self.pump(0.2)

    def idle(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.pump(0.1)

    def send(self, text: str) -> None:
        os.write(self.master, text.encode())

    def close(self) -> None:
        if self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait()
        os.close(self.master)


def runner_argv(stack, tmp_path, *extra):
    return [PYTHON, str(ROOT / "scripts" / "demo.py"), "--url", stack.breaker_url,
            "--no-watcher", "--before-rules", str(baseline_rules_dir(tmp_path / "before_rules")),
            "--presenter-log", str(tmp_path / "presenter.jsonl"), *extra]


def captions(tmp_path) -> list[str]:
    lines = (tmp_path / "presenter.jsonl").read_text().splitlines()
    return [c for c in (json.loads(x).get("caption") for x in lines) if c]


def make_pr_folder(tmp_path, number: int, url: str, verdict: dict) -> dict:
    d = tmp_path / "pr" / str(number)
    rules = baseline_rules_dir(d / "rules")
    (d / "verdict.json").write_text(json.dumps(verdict))
    return {"number": number, "url": url, "head_sha": "a" * 40, "dir": str(d),
            "verdict_file": str(d / "verdict.json"), "rules_dir": str(rules), "applied": True,
            "fetched_at": "2026-11-20T20:00:00Z"}


def test_guided_run_with_the_rehearsal_fallback(stack, tmp_path):
    """--from 4 in a terminal: f uses rehearsal.json, an early Enter never skips a beat,
    case 5 replays the rules of the pull request the breaker holds, and the runner stays up
    after the last beat until q."""
    attack(stack)
    # The verdict file of a pull request is untrusted: wrong id, a wrong URL, an extra field.
    rehearsal = make_pr_folder(tmp_path, 9, REHEARSAL_URL, {
        "incident_id": "inc_0042", "verdict": "contact_client", "reason": "Rehearsal verdict.",
        "evidence": {"basis_ids": [], "prior_payment_ids": [], "extra": 1},
        "pr_url": "https://example.invalid/pull/1", "note": "x"})
    latest = make_pr_folder(tmp_path, 12, OTHER_URL, verdict_body(pr_url=OTHER_URL))
    (tmp_path / "pr" / "rehearsal.json").write_text(json.dumps(rehearsal))
    (tmp_path / "pr" / "latest.json").write_text(json.dumps(latest))

    t = Terminal(runner_argv(stack, tmp_path, "--from", "4", "--pace", "0.3",
                             "--pr-state", str(tmp_path / "pr" / "latest.json")), stack.env)
    try:
        pos = t.expect(r"\[\d\d:\d\d\] \[Enter\] show case 4 live")
        t.send("\n")
        pos = t.expect(r"\[\d\d:\d\d\] waiting for Devin\. f \+ Enter", pos)
        t.send("f\n")
        pos = t.expect(r"This is the pull request from the rehearsal run: #9\.", pos)
        t.send("\n")  # pressed too early, while the captions run: must be discarded
        pos = t.expect(r"\[\d\d:\d\d\] \[Enter\] explain case 5", pos)
        t.idle(1.5)
        assert "Case 5" not in t.out[pos:], "a buffered Enter skipped the pause"

        inc = stack.get("/state")["incident"]
        assert inc["pr_url"] == REHEARSAL_URL and inc["verdict_source"] == "rehearsal"
        assert inc["pause_to_pr_min"] is None
        said = captions(tmp_path)
        assert ("Pull request #9 is from the rehearsal run, with a new SQL rule. "
                "A human reviews and merges it.") in said
        assert not any("minute" in c for c in said)
        assert not any("Devin is working" in c for c in said), "offline: no webhook was sent"

        t.send("\n")
        pos = t.expect(r"\[Enter\] show case 5 live", pos)
        t.send("\n")
        pos = t.expect(r"The demo is finished\. The services keep running until q \+ Enter "
                       r"or Ctrl-C\.", pos, timeout=240)
        pos = t.expect(r"\[\d\d:\d\d\] \[q \+ Enter\] end the demo", pos)
        p = stack.get("/state")["presenter"]
        assert p["active"] and p["step_done"] == [0, 1, 2, 3, 4, 5]
        assert "After: rules from pull request #9 (rehearsal run)" in p["panel"]["text"]
        assert "#12" not in p["panel"]["text"]
        t.send("\n")
        t.idle(1.0)
        assert t.proc.poll() is None, "only q ends the demo"
        t.send("q\n")
        assert t.proc.wait(timeout=30) == 0
        assert stack.get("/state")["presenter"]["active"] is False
    finally:
        t.close()


def test_case5_shows_only_before_when_the_held_pr_has_no_saved_rules(stack, tmp_path):
    """latest.json names another pull request than the one whose verdict the breaker holds."""
    attack(stack)
    assert post_verdict(stack, verdict_body(pr_url=REHEARSAL_URL)).status_code == 200
    (tmp_path / "pr").mkdir()
    latest = make_pr_folder(tmp_path, 12, OTHER_URL, verdict_body(pr_url=OTHER_URL))
    (tmp_path / "pr" / "latest.json").write_text(json.dumps(latest))
    r = subprocess.run(runner_argv(stack, tmp_path, "--auto", "--from", "5", "--pace", "0.02",
                                   "--pr-state", str(tmp_path / "pr" / "latest.json")),
                       cwd=ROOT, env=stack.env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    said = captions(tmp_path)
    assert ("The rules of pull request #9 are not on this laptop, so there is no replay "
            "with them.") in said
    assert not any("After the new rule" in c for c in said)


def test_ctrl_c_prints_the_restart_command(stack, tmp_path):
    env = dict(stack.env, DEMO_SH_FLAGS="--no-webhook")
    t = Terminal(runner_argv(stack, tmp_path, "--from", "4", "--pace", "0.02"), env)
    try:
        t.expect(r"\[Enter\] show case 4 live")
        os.killpg(t.proc.pid, signal.SIGINT)
        t.expect(r"interrupted\. To continue on the same databases: scripts/demo\.sh "
                 r"--keep-data --no-webhook --no-watcher --from 4\n")
        assert t.proc.wait(timeout=30) == 130
        assert "could not clear" not in t.out
    finally:
        t.close()


# --- H: the environment wins over .env ---------------------------------------------------

def test_env_file_fills_in_only_what_is_missing(tmp_path):
    env_file = tmp_path / "env"
    env_file.write_text("JEV_MODE=live\nBANK_URL=http://127.0.0.1:8701\n"
                        "SPACED=\"a b 'c' $HOME\"\nEMPTY=filled\nexport EXPORTED=yes\n# x=1\n")
    script = ('. scripts/_env.sh; load_env_file "$1"; '
              'printf "%s|%s|%s|%s|%s\\n" "$JEV_MODE" "$BANK_URL" "$SPACED" "$EMPTY" "$EXPORTED"; '
              'env | grep -c "^EXPORTED=yes$"')
    env = {k: v for k, v in os.environ.items()
           if k not in ("JEV_MODE", "BANK_URL", "SPACED", "EMPTY", "EXPORTED")}
    env.update(JEV_MODE="stub", SPACED="keep me", EMPTY="")
    r = subprocess.run(["bash", "-c", script, "_", str(env_file)], cwd=ROOT, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == ["stub|http://127.0.0.1:8701|keep me||yes", "1"]


# --- I and J: demo.sh --keep-data after a crash, and clean fixtures ---------------------------

def demo_sh_env(tmp_path, data: Path) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("DEVIN_", "JEV_", "TYPESAFE_", "GITHUB_", "INCIDENTS_DIR",
                                "TRACE_FILE_PUBLIC_DIR", "ADMIN_TOKEN", "SIM_TOKEN"))}
    empty = tmp_path / "empty.env"
    empty.write_text("")
    env.update(PYTHON=PYTHON, PYTHONUNBUFFERED="1", JEV_MODE="stub", DEMO_ENV_FILE=str(empty),
               BANK_DB=str(data / "bank.db"), BREAKER_DB=str(data / "breaker.db"),
               BANK_URL=f"http://127.0.0.1:{replaylib.free_port()}",
               BREAKER_URL=f"http://127.0.0.1:{replaylib.free_port()}",
               DEMO_STATE_DIR=str(tmp_path / "demo"))
    return env


def test_demo_sh_keep_data_continues_after_a_crash(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    env = demo_sh_env(tmp_path, data)
    before = baseline_rules_dir(tmp_path / "before_rules")
    common = ["--no-open", "--no-webhook", "--auto", "--no-watcher", "--pace", "0.02",
              "--attack-delay", "0.02", "--before-rules", str(before),
              "--pr-state", str(tmp_path / "missing.json")]
    committed = {p.name: p.read_bytes() for p in replaylib.INCIDENTS_DIR.glob("*.jsonl")}
    lines: list[str] = []

    p = subprocess.Popen(["bash", str(ROOT / "scripts" / "demo.sh"), *common, "--from", "3"],
                         cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, start_new_session=True)
    reader = threading.Thread(target=lambda: lines.extend(p.stdout), daemon=True)
    reader.start()
    try:
        assert wait_for(lambda: any(x.startswith("Case 4: Devin") for x in lines), 180), \
            "".join(lines)
        os.killpg(p.pid, signal.SIGINT)  # Ctrl-C in the middle of case 4
        assert p.wait(timeout=60) == 130
        reader.join(timeout=10)
    finally:
        if p.poll() is None:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
        remove_runner_traces()
    out = "".join(lines)
    assert "seeding fresh databases" in out
    assert ("To continue on the same databases: scripts/demo.sh --keep-data --no-open "
            "--no-webhook --no-watcher --from 4") in out
    assert port_free(env["BANK_URL"]) and port_free(env["BREAKER_URL"]), "services stopped"
    # The incident trace went to the demo folder; the committed fixtures are unchanged.
    assert (tmp_path / "demo" / "incidents" / "inc_0001.jsonl").is_file()
    assert {p.name: p.read_bytes() for p in replaylib.INCIDENTS_DIR.glob("*.jsonl")} == committed
    assert not (tmp_path / "demo" / "tokens.env").stat().st_mode & 0o077

    r = subprocess.run(["bash", str(ROOT / "scripts" / "demo.sh"), "--keep-data", *common,
                        "--from", "4"], cwd=ROOT, env=env, capture_output=True, text=True,
                       timeout=400)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "keeping the databases (--keep-data), no seed" in out
    assert "seeding fresh databases" not in out
    assert "No incident is open" not in out
    assert "Offline mode: no webhook is configured, so Devin is not called." in out
    assert "The demo is finished" in out
    assert port_free(env["BANK_URL"]) and port_free(env["BREAKER_URL"])


def test_demo_sh_keep_data_without_databases_refuses(tmp_path):
    env = demo_sh_env(tmp_path, tmp_path / "nothing")
    r = subprocess.run(["bash", str(ROOT / "scripts" / "demo.sh"), "--keep-data", "--no-open",
                        "--auto"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 2
    assert "--keep-data, but" in r.stderr
