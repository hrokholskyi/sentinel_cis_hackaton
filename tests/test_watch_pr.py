"""scripts/watch_pr.py against a fake GitHub API (a local http.server thread)."""

import json
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import pytest

import replaylib
from conftest import BASELINE_RULE_FILES

REPO = "acme/demo"
PR_URL = f"https://github.com/{REPO}/pull/7"
SCRIPT = replaylib.ROOT / "scripts" / "watch_pr.py"
RULES = [replaylib.ROOT / "rules" / name for name in BASELINE_RULE_FILES]  # pinned, not the live folder
NEW_RULE = b"-- id: new_rule\n-- level: hard\nSELECT 0 AS violated, 'x' AS detail;\n"
SHA = "a" * 40


def ts(delta_min: float = 0) -> str:
    dt = datetime.now(timezone.utc) + timedelta(minutes=delta_min)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def verdict_doc(**over) -> dict:
    doc = {"incident_id": "inc_0001", "verdict": "contact_client",
           "reason": "Synthetic reason.", "evidence": {"basis_ids": [], "prior_payment_ids": []},
           "pr_url": "https://example.invalid/pull/9"}
    doc.update(over)
    return doc


class FakeGitHub:
    """Implements only: list pulls, get pull, pull files, contents (file raw, directory)."""

    def __init__(self):
        self.pulls = []           # dicts as GitHub returns them
        self.files = {}           # number -> list of filenames
        self.contents = {}        # path -> bytes at the head sha
        self.sizes = {}           # path -> size to report in directory listings
        self.files_calls = 0
        self.hide_verdict_until = 0   # verdict.json is not in the file list before this call
        self.requests = []
        self.script = []          # (status, headers) answered to the next requests, in order
        self.server = None
        self.url = ""

    def add_pr(self, number=7, created=None, files=None, author="devin-ai", head_repo=REPO):
        self.pulls.append({"number": number, "html_url": f"https://github.com/{REPO}/pull/{number}",
                           "title": "Add rule", "created_at": created or ts(),
                           "user": {"login": author},
                           "head": {"sha": SHA, "repo": {"full_name": head_repo}}})
        self.files[number] = files if files is not None else [
            "verdict.json", "reports/inc_0001.md", "rules/07_new_rule.sql"]

    def put_rules(self):
        for f in RULES:
            self.contents[f"rules/{f.name}"] = f.read_bytes()
        self.contents["rules/07_new_rule.sql"] = NEW_RULE
        self.contents["reports/inc_0001.md"] = b"# Report\n"

    def start(self, port=0):
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def send(self, code, body, ctype="application/json"):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def page(self, items, q):
                per, page = int(q.get("per_page", ["30"])[0]), int(q.get("page", ["1"])[0])
                return items[(page - 1) * per: page * per]

            def do_GET(self):
                u = urlparse(self.path)
                q = parse_qs(u.query)
                path = unquote(u.path)
                outer.requests.append(path)
                if outer.script:
                    code, headers = outer.script.pop(0)
                    self.send_response(code)
                    for k, v in headers.items():
                        self.send_header(k, v)
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    self.wfile.write(b"{}")
                    return
                base = f"/repos/{REPO}/"
                if not path.startswith(base):
                    return self.send(404, {})
                rest = path[len(base):]
                if rest == "pulls":
                    if q.get("state") != ["open"]:
                        return self.send(422, {})
                    return self.send(200, self.page(outer.pulls, q))
                if rest.startswith("pulls/"):
                    parts = rest.split("/")
                    number = int(parts[1])
                    pr = next((p for p in outer.pulls
                               if isinstance(p, dict) and p["number"] == number), None)
                    if pr is None:
                        return self.send(404, {})
                    if len(parts) == 2:
                        return self.send(200, pr)
                    if parts[2] == "files":
                        outer.files_calls += 1
                        names = [n for n in outer.files[number]
                                 if not (n.endswith("verdict.json")
                                         and outer.files_calls < outer.hide_verdict_until)]
                        return self.send(200, self.page([{"filename": n, "status": "added"}
                                                         for n in names], q))
                if rest.startswith("contents/"):
                    p = rest[len("contents/"):]
                    if q.get("ref") != [SHA]:
                        return self.send(404, {})
                    if p in outer.contents:
                        return self.send(200, outer.contents[p], "application/octet-stream")
                    kids = [k for k in outer.contents if k.startswith(p + "/")]
                    if kids:
                        return self.send(200, [{"name": k[len(p) + 1:], "type": "file",
                                                "size": outer.sizes.get(k, len(outer.contents[k]))}
                                               for k in sorted(kids)])
                return self.send(404, {})

        self.server = ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        return self.url

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def gh():
    fake = FakeGitHub()
    fake.start()
    yield fake
    fake.stop()


@pytest.fixture
def root(tmp_path):
    (tmp_path / "root").mkdir()
    return tmp_path / "root"


def watch_cmd(gh, root, *args, breaker_url="http://127.0.0.1:9", timeout_min="0.1", api=None):
    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_REPO", "GH_TOKEN")}
    env.update(GITHUB_API_URL=api or gh.url, GITHUB_TOKEN="test-token", BREAKER_URL=breaker_url,
               ADMIN_TOKEN="dev-admin-token")
    return ([replaylib.PYTHON, str(SCRIPT), "--repo", REPO, "--repo-root", str(root),
             "--interval", "0.05", "--timeout-min", timeout_min, *args], env)


def watch(gh, root, *args, **kw):
    cmd, env = watch_cmd(gh, root, *args, **kw)
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120)


def fake_with_verdict(gh, doc=None, **kw):
    gh.add_pr(**kw)
    gh.put_rules()
    gh.contents["verdict.json"] = json.dumps(doc or verdict_doc(), indent=1).encode()


def test_happy_path_after_the_attack(gh, root, stack):
    replaylib.replay(stack.breaker_url, replaylib.LEGIT_TRACE)
    replaylib.replay(stack.breaker_url, replaylib.INCIDENTS_DIR / "inc_0001.jsonl")
    fake_with_verdict(gh, verdict_doc(extra_field="x"), number=7)
    # The pr_url in the file is wrong and there is an unknown field; the watcher fixes both.
    r = watch(gh, root, "--since", ts(-5), breaker_url=stack.breaker_url)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "extra_field" in r.stdout and "differs" in r.stdout
    assert "test-token" not in r.stdout + r.stderr

    inc = next(i for i in stack.get("/state")["incidents"] if i["id"] == "inc_0001")
    assert inc["verdict"] == "contact_client"
    assert inc["pr_url"] == PR_URL

    pr_dir = root / ".state" / "pr" / "7"
    assert (pr_dir / "verdict.json").read_bytes() == gh.contents["verdict.json"]
    assert len(list((pr_dir / "rules").glob("*.sql"))) == 7
    assert (pr_dir / "report.md").read_text() == "# Report\n"
    pr = json.loads((pr_dir / "pr.json").read_text())
    assert pr["number"] == 7 and pr["url"] == PR_URL and pr["head_sha"] == SHA
    assert pr["author"] == "devin-ai" and pr["head_repo"] == REPO and pr["title"] == "Add rule" and pr["created_at"]
    latest = json.loads((root / ".state" / "pr" / "latest.json").read_text())
    assert set(latest) == {"number", "url", "head_sha", "dir", "verdict_file", "rules_dir",
                           "applied", "fetched_at"}
    assert latest["number"] == 7 and latest["url"] == PR_URL and latest["head_sha"] == SHA
    assert latest["dir"] == ".state/pr/7"
    assert latest["verdict_file"] == ".state/pr/7/verdict.json"
    assert latest["rules_dir"] == ".state/pr/7/rules"
    assert latest["applied"] is True and latest["fetched_at"].endswith("Z")


def test_verdict_appears_on_the_third_poll(gh, root):
    fake_with_verdict(gh)
    gh.hide_verdict_until = 3
    r = watch(gh, root, "--since", ts(-5), "--no-apply", "--pr", "7")
    assert r.returncode == 0, r.stdout + r.stderr
    assert gh.files_calls == 3
    assert "waiting for verdict.json" in r.stdout


def test_invalid_json_keeps_polling_then_times_out(gh, root):
    fake_with_verdict(gh)
    gh.contents["verdict.json"] = b"{not json"
    r = watch(gh, root, "--since", ts(-5), "--no-apply", timeout_min="0.02")
    assert r.returncode == 3
    assert gh.files_calls >= 2
    assert not (root / ".state" / "pr" / "latest.json").exists()


def test_old_pull_request_is_ignored_unless_picked_by_number(gh, root):
    fake_with_verdict(gh, created=ts(-60))
    r = watch(gh, root, "--since", ts(-5), "--no-apply", timeout_min="0.02")
    assert r.returncode == 3
    r = watch(gh, root, "--since", ts(-5), "--no-apply", "--pr", "7")
    assert r.returncode == 0, r.stdout + r.stderr


def test_newest_pull_request_with_a_verdict_wins(gh, root):
    fake_with_verdict(gh, number=7, created=ts(-3))
    gh.add_pr(number=8, created=ts(-1), files=["notes.md"])       # newer, no verdict
    gh.add_pr(number=9, created=ts(-2), files=["verdict.json"])   # newer than 7, has a verdict
    r = watch(gh, root, "--since", ts(-5), "--no-apply")
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads((root / ".state" / "pr" / "latest.json").read_text())["number"] == 9


def test_files_list_is_paginated(gh, root):
    fake_with_verdict(gh, files=[f"docs/f{i}.md" for i in range(120)] + ["verdict.json"])
    r = watch(gh, root, "--since", ts(-5), "--no-apply")
    assert r.returncode == 0, r.stdout + r.stderr


def test_malicious_names_and_oversized_files_are_refused(gh, root):
    fake_with_verdict(gh, files=["../../verdict.json", "verdict.json", "reports/../x.md",
                                 "reports/inc_0001.md"])
    for bad in ("rules/a b.sql", "rules/$(id).sql", "rules/.hidden", "rules/evil;rm.sql",
                "rules/sub/deep.sql"):
        gh.contents[bad] = b"SELECT 1;"
    gh.contents["rules/09_big.sql"] = b"-- x\n" * (50 * 1024)  # 250 KB
    r = watch(gh, root, "--since", ts(-5), "--no-apply")
    assert r.returncode == 0, r.stdout + r.stderr
    saved = sorted(p.name for p in (root / ".state" / "pr" / "7" / "rules").iterdir())
    assert saved == sorted([f.name for f in RULES] + ["07_new_rule.sql"])
    assert "refused" in r.stdout
    assert not list(root.rglob("evil*"))
    assert not any(".." in p for p in gh.requests)


def test_unsafe_verdict_path_alone_is_never_fetched(gh, root):
    fake_with_verdict(gh, files=["../../verdict.json", "/etc/verdict.json"])
    r = watch(gh, root, "--since", ts(-5), "--no-apply", timeout_min="0.02")
    assert r.returncode == 3
    assert not any(p.startswith(f"/repos/{REPO}/contents/") for p in gh.requests)


def test_oversized_verdict_is_refused(gh, root):
    fake_with_verdict(gh)
    gh.contents["verdict.json"] = json.dumps(verdict_doc(reason="x" * 300_000)).encode()
    r = watch(gh, root, "--since", ts(-5), "--no-apply", timeout_min="0.02")
    assert r.returncode == 3
    assert not (root / ".state" / "pr").exists()


def test_no_apply_does_not_touch_the_breaker(gh, root, stack):
    fake_with_verdict(gh)
    before = stack.get("/state")
    r = watch(gh, root, "--since", ts(-5), "--no-apply", breaker_url=stack.breaker_url)
    assert r.returncode == 0, r.stdout + r.stderr
    assert stack.get("/state") == before
    latest = json.loads((root / ".state" / "pr" / "latest.json").read_text())
    assert latest["applied"] is False and (root / latest["verdict_file"]).exists()


def test_wrong_incident_id_exits_4_and_keeps_artifacts(gh, root, stack):
    fake_with_verdict(gh, verdict_doc(incident_id="inc_0042"))
    r = watch(gh, root, "--since", ts(-5), breaker_url=stack.breaker_url)
    assert r.returncode == 4, r.stdout + r.stderr
    assert "404" in r.stderr
    latest = json.loads((root / ".state" / "pr" / "latest.json").read_text())
    assert latest["applied"] is False and (root / latest["rules_dir"]).is_dir()


def test_incident_option_ignores_other_incidents(gh, root):
    fake_with_verdict(gh, verdict_doc(incident_id="inc_0042"))
    r = watch(gh, root, "--since", ts(-5), "--no-apply", "--incident", "inc_0001",
              timeout_min="0.02")
    assert r.returncode == 3


def test_repo_from_remote_urls():
    import watch_pr
    assert watch_pr.repo_from_remote("https://github.com/a/b.git") == "a/b"
    assert watch_pr.repo_from_remote("git@github.com:a/b.git\n") == "a/b"
    assert watch_pr.repo_from_remote("https://github.com/a/b") == "a/b"
    assert watch_pr.repo_from_remote("https://example.com/a/b.git") is None


# --- who opened the pull request -------------------------------------------------

def test_fork_pull_request_is_skipped_and_same_repo_one_taken(gh, root):
    fake_with_verdict(gh, number=7, created=ts(-3))
    gh.add_pr(number=8, created=ts(-1), author="stranger", head_repo="evil/demo",
              files=["verdict.json"])   # newer, from a fork
    r = watch(gh, root, "--since", ts(-5), "--no-apply")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("skipped PR #8 by 'stranger'") == 1
    assert "found PR #7 by 'devin-ai'" in r.stdout
    pr = json.loads((root / ".state" / "pr" / "7" / "pr.json").read_text())
    assert pr["author"] == "devin-ai" and pr["head_repo"] == REPO
    assert not (root / ".state" / "pr" / "8").exists()


def test_head_repo_comparison_ignores_letter_case(gh, root):
    fake_with_verdict(gh, head_repo=REPO.upper())
    r = watch(gh, root, "--since", ts(-5), "--no-apply")
    assert r.returncode == 0, r.stdout + r.stderr


def test_deleted_head_repo_counts_as_a_fork(gh, root):
    fake_with_verdict(gh)
    gh.pulls[0]["head"]["repo"] = None
    r = watch(gh, root, "--since", ts(-5), "--no-apply", timeout_min="0.02")
    assert r.returncode == 3 and "skipped PR #7" in r.stdout


def test_author_allowlist(gh, root):
    fake_with_verdict(gh, author="Devin-AI")
    r = watch(gh, root, "--since", ts(-5), "--no-apply", "--authors", "someone,else",
              timeout_min="0.02")
    assert r.returncode == 3
    assert r.stdout.count("skipped PR #7 by 'Devin-AI'") == 1
    r = watch(gh, root, "--since", ts(-5), "--no-apply", "--authors", "someone, DEVIN-ai")
    assert r.returncode == 0, r.stdout + r.stderr


def test_author_allowlist_from_the_environment(gh, root, monkeypatch):
    fake_with_verdict(gh)
    monkeypatch.setenv("WATCH_PR_AUTHORS", "nobody")
    r = watch(gh, root, "--since", ts(-5), "--no-apply", timeout_min="0.02")
    assert r.returncode == 3
    monkeypatch.setenv("WATCH_PR_AUTHORS", "Devin-AI")
    r = watch(gh, root, "--since", ts(-5), "--no-apply")
    assert r.returncode == 0, r.stdout + r.stderr


def test_pr_option_refuses_a_fork_with_exit_2(gh, root):
    fake_with_verdict(gh, author="stranger", head_repo="evil/demo")
    r = watch(gh, root, "--no-apply", "--pr", "7")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "refusing PR #7" in r.stderr and "--allow-forks" in r.stderr
    assert not (root / ".state").exists()


def test_pr_option_refuses_an_author_not_in_the_allowlist(gh, root):
    fake_with_verdict(gh)
    r = watch(gh, root, "--no-apply", "--pr", "7", "--authors", "other")
    assert r.returncode == 2 and "not in the allowed authors" in r.stderr


def test_allow_forks_accepts_a_fork(gh, root):
    fake_with_verdict(gh, author="stranger", head_repo="evil/demo")
    r = watch(gh, root, "--no-apply", "--pr", "7", "--allow-forks")
    assert r.returncode == 0, r.stdout + r.stderr
    pr = json.loads((root / ".state" / "pr" / "7" / "pr.json").read_text())
    assert pr["author"] == "stranger" and pr["head_repo"] == "evil/demo"


# --- the shape of API answers ------------------------------------------------------

def test_malformed_list_entries_are_skipped_without_a_crash(gh, root):
    fake_with_verdict(gh, number=7, created=ts(-3))
    good = gh.pulls[0]
    bad = [
        "a string", 5, None, ["list"],
        {**good, "number": "../../../etc"},
        {**good, "number": True},
        {**good, "number": 0},
        {**good, "number": -3},
        {**good, "number": 1.5},
        {**good, "number": 9, "head": {"sha": "../x", "repo": {"full_name": REPO}}},
        {**good, "number": 10, "head": "nope"},
        {**good, "number": 11, "html_url": "file:///etc/passwd"},
        {**good, "number": 13, "created_at": "yesterday"},
        {**good, "number": 14, "user": "devin"},
    ]
    gh.pulls[:] = bad + [good]
    r = watch(gh, root, "--since", ts(-5), "--no-apply")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Traceback" not in r.stderr
    assert "skipped a malformed pull request entry" in r.stdout
    assert json.loads((root / ".state" / "pr" / "latest.json").read_text())["number"] == 7
    assert sorted(p.name for p in (root / ".state" / "pr").iterdir()) == ["7", "latest.json"]
    assert sorted(p.name for p in root.iterdir()) == [".state"]


def test_non_integer_number_is_never_used_as_a_path(gh, root):
    fake_with_verdict(gh)
    gh.pulls[0]["number"] = "../../escape"
    r = watch(gh, root, "--since", ts(-5), "--no-apply", timeout_min="0.02")
    assert r.returncode == 3 and "Traceback" not in r.stderr
    assert not (root / ".state").exists()
    assert not list(root.parent.glob("escape*"))


def test_non_dict_file_entries_do_not_crash():
    import watch_pr
    files = ["x", 3, None, {"filename": "verdict.json", "status": "added"}]
    assert watch_pr.find_verdict_path(files) == "verdict.json"
    assert watch_pr.find_report_path(files) is None


def test_url_and_shape_rules():
    import watch_pr
    pr = {"number": 1, "html_url": "https://github.com/a/b/pull/1", "head": {"sha": "abc1234"}}
    assert watch_pr.check_shape(pr, "https://api.github.com") is None
    assert watch_pr.check_shape({**pr, "html_url": "http://x/y"}, "https://api.github.com")
    assert watch_pr.check_shape({**pr, "html_url": "http://x/y"}, "http://127.0.0.1:1") is None
    assert watch_pr.check_shape({**pr, "head": {"sha": "ABC1234"}}, "https://a")
    assert watch_pr.check_shape({**pr, "head": {"sha": "abc12"}}, "https://a")


# --- transient errors, authentication errors, the rehearsal pointer -------------------

def test_http_5xx_is_retried(gh, root):
    fake_with_verdict(gh)
    gh.script = [(503, {}), (502, {})]
    r = watch(gh, root, "--pr", "7", "--no-apply")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("retrying in") == 2
    assert (root / ".state" / "pr" / "latest.json").exists()


def test_connection_refused_is_retried_until_github_is_back(tmp_path, root):
    """GitHub is unreachable when the watcher starts, then comes up: the watcher carries on."""
    port = replaylib.free_port()
    fake = FakeGitHub()
    cmd, env = watch_cmd(fake, root, "--pr", "7", "--no-apply", timeout_min="0.5",
                         api=f"http://127.0.0.1:{port}")
    with open(tmp_path / "watch.log", "w+") as log:
        proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, text=True)
        try:
            time.sleep(1.5)
            fake_with_verdict(fake)
            fake.start(port)
            assert proc.wait(timeout=60) == 0
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            if fake.server:
                fake.stop()
        log.seek(0)
        out = log.read()
    assert "ConnectError" in out and "retrying in" in out
    assert "done: saved PR #7" in out


def test_unreachable_github_exits_5_at_the_deadline(root):
    fake = FakeGitHub()
    r = watch(fake, root, "--pr", "7", "--no-apply", timeout_min="0.05",
              api=f"http://127.0.0.1:{replaylib.free_port()}")
    assert r.returncode == 5, r.stdout + r.stderr
    assert r.stdout.count("retrying in") >= 2
    assert "still failing at the deadline" in r.stderr


def test_401_is_an_authentication_error(gh, root):
    fake_with_verdict(gh)
    gh.script = [(401, {})]
    r = watch(gh, root, "--pr", "7", "--no-apply")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "HTTP 401" in r.stderr and "GITHUB_TOKEN" in r.stderr
    assert "rate limit" not in r.stdout + r.stderr
    assert "test-token" not in r.stdout + r.stderr


def test_403_without_rate_limit_information_is_an_authentication_error(gh, root):
    fake_with_verdict(gh)
    gh.script = [(403, {"X-RateLimit-Remaining": "4999"})]
    r = watch(gh, root, "--pr", "7", "--no-apply")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "HTTP 403" in r.stderr and "rate limit" not in r.stdout + r.stderr


def test_403_rate_limit_is_waited_out(gh, root):
    fake_with_verdict(gh)
    gh.script = [(403, {"X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(int(time.time()) + 1)})]
    r = watch(gh, root, "--pr", "7", "--no-apply")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "GitHub rate limit, sleeping" in r.stdout


def test_save_as_rehearsal_writes_its_own_pointer(gh, root):
    fake_with_verdict(gh)
    (root / ".state" / "pr").mkdir(parents=True)
    latest = root / ".state" / "pr" / "latest.json"
    latest.write_text('{"number": 3}\n')
    r = watch(gh, root, "--pr", "7", "--no-apply", "--save-as", "rehearsal")
    assert r.returncode == 0, r.stdout + r.stderr
    assert latest.read_text() == '{"number": 3}\n', "a rehearsal save leaves latest.json alone"
    reh = json.loads((root / ".state" / "pr" / "rehearsal.json").read_text())
    assert set(reh) == {"number", "url", "head_sha", "dir", "verdict_file", "rules_dir",
                        "applied", "fetched_at"}
    assert reh["url"] == PR_URL and reh["rules_dir"] == ".state/pr/7/rules"
    assert reh["applied"] is False
    assert len(list((root / ".state" / "pr" / "7" / "rules").glob("*.sql"))) == 7
