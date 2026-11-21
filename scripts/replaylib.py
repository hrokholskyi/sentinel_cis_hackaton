"""Replay traces against a fresh bank and breaker. Used by replay.sh, the snapshot builder
(facts.sh, infra_check.sh) and the tests. Needs no network and no secrets.

API, for a regression test that replays an incident trace:

    import sys; sys.path.insert(0, "scripts")
    import replaylib

    def test_incident_is_fully_held(tmp_path):
        with replaylib.start(tmp_path) as stack:       # seeds tmp_path, starts both services
            legit = replaylib.replay(stack.breaker_url, replaylib.LEGIT_TRACE)
            attack = replaylib.replay(stack.breaker_url,
                                      replaylib.ROOT / "fixtures/incidents/inc_0001.jsonl")
        assert attack.executed == 0
        assert legit.executed == legit.sent

Always replay fixtures/legit.jsonl first: once an attack pauses the key, every later payment
from that key is held. replay() returns a Result with sent, executed, held, rejected and the
per-request outcomes in `lines`. start() takes rules_dir= to load rules from another folder
and env= for extra environment variables.
"""

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, Union

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import common  # noqa: E402

LEGIT_TRACE = ROOT / "fixtures" / "legit.jsonl"
INCIDENTS_DIR = ROOT / "fixtures" / "incidents"
STATE_DIR = ROOT / ".state"
PYTHON = sys.executable

PathLike = Union[str, Path]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def latest_incident_trace(incidents_dir: PathLike = INCIDENTS_DIR) -> Path:
    """The incident trace with the highest file name, e.g. fixtures/incidents/inc_0001.jsonl."""
    files = sorted(Path(incidents_dir).glob("*.jsonl"))
    if not files:
        raise FileNotFoundError(f"no incident traces in {incidents_dir}")
    return files[-1]


def base_env(workdir: PathLike, rules_dir: Optional[PathLike] = None) -> dict:
    """Environment for services and scripts on databases in workdir: stub Jev, no webhook."""
    workdir = Path(workdir)
    env = dict(os.environ)
    env.update({
        "BANK_DB": str(workdir / "bank.db"),
        "BREAKER_DB": str(workdir / "breaker.db"),
        "INCIDENTS_DIR": str(workdir / "incidents"),
        "RULES_DIR": str(Path(rules_dir).resolve() if rules_dir else ROOT / "rules"),
        "DEMO_MODE": "1",
        "JEV_MODE": "stub",
    })
    for name in ("DEVIN_WEBHOOK_URL", "DEVIN_WEBHOOK_SECRET"):
        env.pop(name, None)
    return env


def seed(env: dict) -> None:
    """Create fresh bank.db and breaker.db at env BANK_DB and BREAKER_DB."""
    r = subprocess.run([PYTHON, str(ROOT / "scripts" / "seed.py")], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"seed failed: {r.stderr.strip()}")


def wait_healthy(url: str, proc: subprocess.Popen, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"{url} exited early with code {proc.returncode}")
        try:
            if httpx.get(url + "/health", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"{url} did not become healthy")


@dataclass
class Stack:
    """A running bank and breaker on free ports, with their databases in workdir."""
    workdir: Path
    env: dict
    bank_url: str
    breaker_url: str
    procs: list = field(default_factory=list)  # (Popen, log file)

    @property
    def tmp(self) -> Path:
        return self.workdir

    def get(self, path: str):
        return httpx.get(self.breaker_url + path, timeout=5).json()

    def run_script(self, name: str, *args: str) -> subprocess.CompletedProcess:
        """Run scripts/<name> with this stack's environment."""
        return subprocess.run([PYTHON, str(ROOT / "scripts" / name), *args], cwd=ROOT,
                              env=self.env, capture_output=True, text=True, timeout=120)

    def stop(self) -> None:
        """Stop both services by pid. Safe to call more than once."""
        for proc, log in self.procs:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            log.close()
        self.procs = []


@contextmanager
def start(workdir: PathLike, *, rules_dir: Optional[PathLike] = None, env: Optional[dict] = None,
          seed_first: bool = True, pidfile: Optional[PathLike] = None) -> Iterator[Stack]:
    """Seed databases in workdir (unless seed_first=False) and run bank and breaker.

    env adds or overrides environment variables. pidfile, if given, gets one service pid
    per line, so a shell trap can stop them if this process dies. Both services always stop
    when the block ends.
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    full_env = base_env(workdir, rules_dir)
    full_env.update(env or {})
    bank_port, breaker_port = free_port(), free_port()
    full_env["BANK_URL"] = f"http://127.0.0.1:{bank_port}"
    full_env["BREAKER_URL"] = f"http://127.0.0.1:{breaker_port}"
    if seed_first:
        seed(full_env)
    stack = Stack(workdir, full_env, full_env["BANK_URL"], full_env["BREAKER_URL"])
    try:
        for module, port, url in (("bank.app:app", bank_port, stack.bank_url),
                                  ("breaker.app:app", breaker_port, stack.breaker_url)):
            log = open(workdir / f"{module.split('.')[0]}.log", "w")
            proc = subprocess.Popen(
                [PYTHON, "-m", "uvicorn", module, "--host", "127.0.0.1", "--port", str(port)],
                cwd=ROOT, env=full_env, stdout=log, stderr=subprocess.STDOUT)
            stack.procs.append((proc, log))
            if pidfile:
                with open(pidfile, "a") as f:
                    f.write(f"{proc.pid}\n")
            wait_healthy(url, proc)
        yield stack
    finally:
        stack.stop()


def read_trace(path: PathLike) -> list[dict]:
    """Lines {sim_time, request, status, reservation_id} of a JSONL trace."""
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


@dataclass
class Result:
    name: str
    sent: int = 0
    executed: int = 0
    held: int = 0
    rejected: int = 0
    lines: list = field(default_factory=list)  # {sim_time, request, status, reservation_id, recorded}


def replay(breaker_url: str, trace: PathLike, name: Optional[str] = None) -> Result:
    """POST every request of a trace to the breaker with its sim_time; count the outcomes."""
    out = Result(name or Path(trace).stem)
    with httpx.Client(base_url=breaker_url.rstrip("/"), timeout=10.0) as client:
        for line in read_trace(trace):
            r = client.post("/payouts", json=line["request"],
                            headers={"X-API-Key": common.API_KEY_SECRET,
                                     "X-Sim-Time": line["sim_time"]})
            try:
                data = r.json()
            except ValueError:
                data = {}
            if r.status_code == 200:
                status = "executed"
            elif r.status_code == 202:
                status = data.get("status", "held")
            else:
                status = "rejected"
            out.sent += 1
            if status == "executed":
                out.executed += 1
            elif status == "held":
                out.held += 1
            else:
                out.rejected += 1
            out.lines.append({"sim_time": line["sim_time"], "request": line["request"],
                              "status": status, "reservation_id": data.get("reservation_id"),
                              "recorded": line.get("status")})
    return out


# --- replay.sh ------------------------------------------------------------------

def attack_passes(r: Result) -> bool:
    return r.sent > 0 and r.executed == 0


def legit_passes(r: Result) -> bool:
    return r.sent > 0 and r.executed == r.sent


def table(attack: Result, legit: Result) -> str:
    """The SPEC section 10 result table."""
    def row(label, r, ok, rule):
        return f"{label:<20}{r.sent:>5}{r.executed:>9}{r.held:>9}     {'PASS' if ok else 'FAIL'} ({rule})"
    return "\n".join([
        f"{'':<22}sent   executed   held",
        row("attack", attack, attack_passes(attack), "executed must be 0"),
        row("legit", legit, legit_passes(legit), "executed must be 100%"),
    ])


def run(incident_trace: PathLike, workdir: PathLike, *, rules_dir: Optional[PathLike] = None,
        pidfile: Optional[PathLike] = None) -> tuple[Result, Result]:
    """Fresh databases in workdir, legit trace first, incident trace second, same instance."""
    with start(workdir, rules_dir=rules_dir, pidfile=pidfile) as stack:
        legit = replay(stack.breaker_url, LEGIT_TRACE, "legit")
        attack = replay(stack.breaker_url, incident_trace, "attack")
    return attack, legit


def _exit_on_term(*_):
    raise SystemExit(130)


def main(argv: list[str]) -> int:
    """replay.sh [incident_id] [--rules-dir DIR]: print the table, exit 0 only if both pass."""
    signal.signal(signal.SIGTERM, _exit_on_term)
    import argparse
    ap = argparse.ArgumentParser(prog="scripts/replay.sh", description=main.__doc__)
    ap.add_argument("incident_id", nargs="?", help="default: latest in fixtures/incidents/")
    ap.add_argument("--workdir", help="temp dir for databases and logs (default: a new one)")
    ap.add_argument("--pidfile", help="append service pids here (used by replay.sh)")
    ap.add_argument("--rules-dir", help="load rules/*.sql from this folder instead of rules/")
    args = ap.parse_args(argv)
    rules_dir = None
    if args.rules_dir:
        rules_dir = Path(args.rules_dir).resolve()
        if not any(rules_dir.glob("*.sql")):
            print(f"no rule files in {args.rules_dir}", file=sys.stderr)
            return 2
    if args.incident_id:
        if not all(ch.isalnum() or ch == "_" for ch in args.incident_id):
            print(f"invalid incident id: {args.incident_id}", file=sys.stderr)
            return 2
        trace = INCIDENTS_DIR / f"{args.incident_id}.jsonl"
        if not trace.is_file():
            print(f"no trace file {trace.relative_to(ROOT)}", file=sys.stderr)
            return 2
    else:
        trace = latest_incident_trace()
    own = args.workdir is None
    workdir = Path(args.workdir or tempfile.mkdtemp(prefix="replay."))
    try:
        attack, legit = run(trace, workdir, rules_dir=rules_dir, pidfile=args.pidfile)
    finally:
        if own:
            shutil.rmtree(workdir, ignore_errors=True)
    rules_note = "" if rules_dir is None else f", rules from {args.rules_dir}"
    print(f"replay of {trace.relative_to(ROOT)} after {LEGIT_TRACE.relative_to(ROOT)}"
          f" (fresh seed, JEV_MODE=stub, no webhook{rules_note})")
    print(table(attack, legit))
    return 0 if attack_passes(attack) and legit_passes(legit) else 1


# --- snapshot for the investigator scripts ----------------------------------------

def _git(*args: str) -> Optional[str]:
    try:
        r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def _rules_at_trace_commit(trace: Path, dest: Path) -> str:
    """Copy rules/*.sql as of the commit that last changed the trace into dest.

    The snapshot must show the state of the incident as it happened, even after a new rule
    was added. Falls back to the working tree rules when git cannot tell.
    """
    rel = trace.relative_to(ROOT).as_posix()
    commit = (_git("log", "-1", "--format=%H", "--", rel) or "").strip()
    names = _git("ls-tree", "--name-only", f"{commit}:rules") if commit else None
    dest.mkdir(parents=True, exist_ok=True)
    if names:
        for name in names.split():
            if name.endswith(".sql"):
                text = _git("show", f"{commit}:rules/{name}")
                if text is not None:
                    (dest / name).write_text(text)
        if any(dest.glob("*.sql")):
            return f"rules as of commit {commit[:9]}"
    for path in sorted((ROOT / "rules").glob("*.sql")):
        shutil.copy(path, dest / path.name)
    return "rules from the working tree"


def _sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(state_dir: PathLike = STATE_DIR) -> tuple[Path, Path]:
    """Rebuild the incident state for an investigator who has only a git clone.

    Seeds into state_dir, runs bank and breaker there (stub Jev, no webhook), replays the
    legit trace and then the latest incident trace, and stops the services. The seed is
    deterministic, so this reproduces the incident. Reused while the incident trace is
    unchanged. Returns (bank_db, breaker_db).
    """
    state_dir = Path(state_dir)
    trace = latest_incident_trace()
    stamp = {"trace": trace.relative_to(ROOT).as_posix(), "sha256": _sha256(trace)}
    marker = state_dir / "snapshot.json"
    bank_db, breaker_db = state_dir / "bank.db", state_dir / "breaker.db"
    try:
        if json.loads(marker.read_text()) == stamp and bank_db.is_file() and breaker_db.is_file():
            return bank_db, breaker_db
    except (OSError, ValueError):
        pass
    shutil.rmtree(state_dir, ignore_errors=True)
    state_dir.mkdir(parents=True)
    rules_note = _rules_at_trace_commit(trace, state_dir / "rules")
    with start(state_dir, rules_dir=state_dir / "rules") as stack:
        replay(stack.breaker_url, LEGIT_TRACE, "legit")
        attack = replay(stack.breaker_url, trace, "attack")
    differs = sum(1 for x in attack.lines if x["recorded"] and x["status"] != x["recorded"])
    marker.write_text(json.dumps(stamp))
    rel = state_dir.relative_to(ROOT) if state_dir.is_relative_to(ROOT) else state_dir
    print(f"note: no databases found; built a snapshot in {rel}/ by replaying "
          f"{LEGIT_TRACE.relative_to(ROOT)} and {stamp['trace']} on a fresh seed ({rules_note})"
          + (f"; WARNING: {differs} outcomes differ from the trace" if differs else ""),
          file=sys.stderr)
    return bank_db, breaker_db


def resolve_databases() -> tuple[Path, Path]:
    """BANK_DB and BREAKER_DB if set and existing, else bank.db and breaker.db in the repo root
    if both exist, else the snapshot."""
    env_bank, env_breaker = os.environ.get("BANK_DB"), os.environ.get("BREAKER_DB")
    if env_bank and env_breaker and Path(env_bank).is_file() and Path(env_breaker).is_file():
        return Path(env_bank), Path(env_breaker)
    if (ROOT / "bank.db").is_file() and (ROOT / "breaker.db").is_file():
        return ROOT / "bank.db", ROOT / "breaker.db"
    return snapshot()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
