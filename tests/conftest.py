"""Shared harness: seed temp databases and run the real bank and breaker as subprocesses."""

import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Stack:
    tmp: Path
    env: dict
    bank_url: str
    breaker_url: str
    procs: list = field(default_factory=list)

    def run_script(self, name: str, *args: str) -> subprocess.CompletedProcess:
        """Run scripts/<name> against this stack and return the finished process."""
        return subprocess.run(
            [PYTHON, str(ROOT / "scripts" / name), *args],
            cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=120,
        )

    def get(self, path: str):
        return httpx.get(self.breaker_url + path, timeout=5).json()


def wait_healthy(url: str, proc: subprocess.Popen, timeout: float = 15) -> None:
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


@pytest.fixture
def stack(tmp_path):
    bank_port, breaker_port = free_port(), free_port()
    env = dict(os.environ)
    env.update({
        "BANK_DB": str(tmp_path / "bank.db"),
        "BREAKER_DB": str(tmp_path / "breaker.db"),
        "BANK_URL": f"http://127.0.0.1:{bank_port}",
        "BREAKER_URL": f"http://127.0.0.1:{breaker_port}",
        "INCIDENTS_DIR": str(tmp_path / "incidents"),
        "DEMO_MODE": "1",
        "JEV_MODE": "stub",
    })
    env.pop("DEVIN_WEBHOOK_URL", None)
    seed = subprocess.run([PYTHON, str(ROOT / "scripts" / "seed.py")], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=60)
    assert seed.returncode == 0, seed.stderr

    s = Stack(tmp=tmp_path, env=env, bank_url=env["BANK_URL"], breaker_url=env["BREAKER_URL"])
    try:
        for module, port, url in (("bank.app:app", bank_port, s.bank_url),
                                  ("breaker.app:app", breaker_port, s.breaker_url)):
            log = open(tmp_path / f"{module.split('.')[0]}.log", "w")
            proc = subprocess.Popen(
                [PYTHON, "-m", "uvicorn", module, "--host", "127.0.0.1", "--port", str(port)],
                cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
            )
            s.procs.append((proc, log))
            wait_healthy(url, proc)
        yield s
    finally:
        for proc, log in s.procs:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            log.close()
