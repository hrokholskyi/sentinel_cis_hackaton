"""Host header check against DNS rebinding, and the demo watcher command (findings 7 and 11)."""

import httpx

from conftest import ROOT


def test_both_services_refuse_a_foreign_host(stack):
    for url in (stack.bank_url, stack.breaker_url):
        assert httpx.get(url + "/health", headers={"Host": "evil.example"}, timeout=5).status_code == 400
        port = url.rsplit(":", 1)[1]
        for host in ("127.0.0.1", "localhost"):
            r = httpx.get(url + "/health", headers={"Host": f"{host}:{port}"}, timeout=5)
            assert r.status_code == 200, (url, host)
    r = httpx.get(stack.breaker_url + "/state", headers={"Host": "rebind.attacker.test"}, timeout=5)
    assert r.status_code == 400


def test_allowed_hosts_from_env(make_stack):
    stack = make_stack(env={"ALLOWED_HOSTS": "127.0.0.1, stage.local"})
    for url in (stack.bank_url, stack.breaker_url):
        assert httpx.get(url + "/health", headers={"Host": "stage.local"}, timeout=5).status_code == 200
        assert httpx.get(url + "/health", headers={"Host": "localhost"}, timeout=5).status_code == 400


def test_demo_runner_starts_the_watcher_for_the_open_incident(tmp_path, monkeypatch):
    import importlib.util
    import subprocess
    from types import SimpleNamespace

    spec = importlib.util.spec_from_file_location("demo_runner", ROOT / "scripts" / "demo.py")
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    calls = []

    class FakePopen:
        pid = 4242

        def __init__(self, argv, **kwargs):
            calls.append(argv)

    monkeypatch.setattr(demo, "ROOT", tmp_path)
    monkeypatch.setattr(demo, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    args = SimpleNamespace(url="http://127.0.0.1:1", before_rules=None, no_watcher=False,
                           watcher=str(ROOT / "scripts" / "watch_pr.py"))
    runner = demo.Runner(args)
    try:
        runner.start_watcher({"incident": {"id": "inc_0007", "status": "open", "pr_url": None,
                                           "tripped_wall_at": "2026-11-21T10:00:00+00:00"}})
    finally:
        runner.watcher_log.close()
        runner.http.close()
    argv = calls[0]
    assert argv[argv.index("--incident") + 1] == "inc_0007"
