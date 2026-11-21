#!/usr/bin/env python3
"""Pick up the pull request that the external agent (Devin) opened and apply its verdict.

Polls the GitHub API for an open pull request that contains a verdict.json, saves the
artifacts under .state/pr/<number>/, writes .state/pr/latest.json and POSTs the verdict to the
breaker (the same call as scripts/apply_verdict.sh).

Everything that comes from the pull request is untrusted data: nothing is executed, file
paths are validated, downloads are capped, and the text is never used in a shell command.

Network errors, timeouts and HTTP 5xx from GitHub are retried with a short backoff until the
deadline. HTTP 401, and 403 without rate-limit information, are authentication errors.

Exit codes: 0 done, 2 usage, setup or authentication error, 3 timeout, 4 the breaker refused
the verdict or could not be reached, 5 GitHub API error.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlparse

import httpx

ROOT = Path(__file__).resolve().parent.parent
MAX_FILE_BYTES = 200 * 1024
MAX_LIST_BYTES = 4 * 1024 * 1024
RETRY_FIRST_S = 0.5   # backoff for network errors and HTTP 5xx: 0.5, 1, 2, 4, 5, 5, ... seconds
RETRY_MAX_S = 5.0
POINTER_NAMES = ("latest", "rehearsal")
RULE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
INCIDENT_ID = re.compile(r"^[A-Za-z0-9_]+$")
SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")
VERDICT_FIELDS = ("incident_id", "verdict", "reason", "evidence", "pr_url")
EVIDENCE_FIELDS = ("basis_ids", "prior_payment_ids")

QUIET = False


class WatchError(Exception):
    def __init__(self, msg: str, code: int):
        super().__init__(msg)
        self.code = code


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(text: str) -> datetime:
    dt = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def say(msg: str) -> None:
    if not QUIET:
        print(f"[{now_utc().strftime('%H:%M:%S')}] {msg}", flush=True)


def warn(msg: str) -> None:
    if not QUIET:
        print(f"[{now_utc().strftime('%H:%M:%S')}] warning: {msg}", flush=True)


# --- configuration ------------------------------------------------------------

def repo_from_remote(url: str) -> Optional[str]:
    m = re.match(r"^(?:https?://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)"
                 r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$", url.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def detect_repo(root: Path) -> Optional[str]:
    try:
        out = subprocess.run(["git", "remote", "get-url", "origin"], cwd=root, text=True,
                             capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return repo_from_remote(out.stdout) if out.returncode == 0 else None


def find_token() -> Optional[str]:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return token
    if shutil.which("gh"):
        try:
            out = subprocess.run(["gh", "auth", "token"], text=True, capture_output=True,
                                 timeout=10)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def is_loopback(url: str) -> bool:
    return (urlparse(url).hostname or "") in ("127.0.0.1", "localhost", "::1")


# --- GitHub client --------------------------------------------------------------

class GitHub:
    def __init__(self, base_url: str, repo: str, token: Optional[str], deadline: float):
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "trustgap-watch-pr"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.client = httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=30,
                                   trust_env=not is_loopback(base_url))
        self.repo = repo
        self.deadline = deadline

    def close(self) -> None:
        self.client.close()

    def _backoff(self, what: str, attempt: int) -> None:
        """Sleep before retrying a transient error; WatchError(5) if the deadline would pass."""
        wait = min(RETRY_MAX_S, RETRY_FIRST_S * 2 ** attempt)
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise WatchError(f"GitHub API still failing at the deadline: {what}", 5)
        wait = min(wait, left)
        warn(f"GitHub API: {what}, retrying in {wait:.1f}s")
        time.sleep(wait)

    def _wait_rate_limit(self, resp: httpx.Response) -> None:
        wait = 60.0
        reset = resp.headers.get("x-ratelimit-reset")
        retry = resp.headers.get("retry-after")
        try:
            if retry:
                wait = min(wait, float(retry))
            elif reset:
                wait = min(wait, max(0.0, float(reset) - time.time()))
        except ValueError:
            pass
        if time.monotonic() + wait > self.deadline:
            raise WatchError("GitHub rate limit reached and the timeout would pass first", 3)
        warn(f"GitHub rate limit, sleeping {wait:.0f}s")
        time.sleep(wait)

    def get(self, path: str, params: Optional[dict] = None, raw: bool = False,
            cap: int = MAX_LIST_BYTES) -> Optional[bytes]:
        """GET one API path. Returns the body bytes, None on 404; raises on other errors.

        Raises WatchError(code 6) when the body is larger than `cap`.
        """
        headers = {"Accept": "application/vnd.github.raw+json"} if raw else {}
        limited, failed = 0, 0
        while True:
            try:
                with self.client.stream("GET", path, params=params, headers=headers) as resp:
                    code = resp.status_code
                    if code == 401:
                        raise WatchError(f"GitHub refused the credentials (HTTP 401 on {path}):"
                                         " check GITHUB_TOKEN or run gh auth login", 2)
                    if code == 429 or (code == 403 and is_rate_limited(resp)):
                        resp.read()
                        limited += 1
                        if limited > 20:
                            raise WatchError("GitHub API: too many rate limit retries", 5)
                        self._wait_rate_limit(resp)
                        continue
                    if code == 403:
                        raise WatchError(f"GitHub denied access (HTTP 403 on {path}): the token"
                                         f" cannot read {self.repo}, check GITHUB_TOKEN", 2)
                    if code == 404:
                        return None
                    if code >= 500:
                        resp.read()
                        self._backoff(f"HTTP {code} on {path}", failed)
                        failed += 1
                        continue
                    if code != 200:
                        raise WatchError(f"GitHub API {path}: HTTP {code}", 5)
                    body = b""
                    for chunk in resp.iter_bytes():
                        body += chunk
                        if len(body) > cap:
                            raise WatchError(f"{path}: larger than {cap} bytes", 6)
                    return body
            except httpx.UnsupportedProtocol as e:
                raise WatchError(f"GitHub API URL is not usable: {e}", 2)
            except (httpx.TransportError, httpx.DecodingError) as e:
                # connection refused, DNS, timeouts, a dropped connection: transient
                self._backoff(f"{type(e).__name__} on {path}", failed)
                failed += 1
            except httpx.HTTPError as e:
                raise WatchError(f"GitHub API {path}: {type(e).__name__}", 5)

    def json(self, path: str, params: Optional[dict] = None):
        body = self.get(path, params)
        if body is None:
            return None
        try:
            return json.loads(body)
        except ValueError:
            raise WatchError(f"GitHub API {path}: answer is not JSON", 5)

    def paged(self, path: str, max_pages: int = 30, params: Optional[dict] = None) -> list:
        items: list = []
        for page in range(1, max_pages + 1):
            chunk = self.json(path, {**(params or {}), "per_page": 100, "page": page})
            if not isinstance(chunk, list):
                break
            items.extend(chunk)
            if len(chunk) < 100:
                break
        return items

    def open_pulls(self) -> list:
        return self.paged(f"/repos/{self.repo}/pulls", max_pages=10,
                          params={"state": "open", "sort": "created", "direction": "desc"})

    def pull(self, number: int):
        return self.json(f"/repos/{self.repo}/pulls/{number}")

    def files(self, number: int) -> list:
        return self.paged(f"/repos/{self.repo}/pulls/{number}/files")

    def file_at(self, path: str, sha: str, cap: int = MAX_FILE_BYTES) -> Optional[bytes]:
        return self.get(f"/repos/{self.repo}/contents/{quote(path, safe='/')}", {"ref": sha},
                        raw=True, cap=cap)

    def list_dir(self, path: str, sha: str):
        return self.json(f"/repos/{self.repo}/contents/{quote(path, safe='/')}", {"ref": sha})


def is_rate_limited(resp: httpx.Response) -> bool:
    """A 403 is a rate limit only when GitHub says so: Retry-After, or no requests left."""
    return bool(resp.headers.get("retry-after")) or resp.headers.get("x-ratelimit-remaining") == "0"


# --- validation of untrusted paths ----------------------------------------------

def safe_repo_path(path) -> bool:
    if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path or "\0" in path:
        return False
    return all(p not in ("", ".", "..") for p in path.split("/"))


def find_verdict_path(files: list) -> Optional[str]:
    """Path of a verdict.json among the changed files; the one at the repo root wins."""
    found = []
    for f in files:
        if not isinstance(f, dict):
            continue
        name = f.get("filename")
        if f.get("status") == "removed":
            continue
        if isinstance(name, str) and name.split("/")[-1] == "verdict.json":
            if safe_repo_path(name):
                found.append(name)
            else:
                warn(f"refused unsafe file name in the pull request: {name!r}")
    found.sort(key=lambda p: (p.count("/"), p))
    return found[0] if found else None


def find_report_path(files: list) -> Optional[str]:
    for f in files:
        name = f.get("filename") if isinstance(f, dict) else None
        if (isinstance(name, str) and f.get("status") != "removed" and safe_repo_path(name)
                and name.startswith("reports/") and name.endswith(".md")
                and RULE_NAME.match(name[len("reports/"):])):
            return name
    return None


def clean_verdict(data) -> tuple[dict, list[str]]:
    """Keep only the fields the breaker endpoint accepts. Returns (body, dropped names)."""
    body = {k: data[k] for k in VERDICT_FIELDS if k in data}
    dropped = [k for k in data if k not in VERDICT_FIELDS]
    ev = body.get("evidence")
    if isinstance(ev, dict):
        dropped += [f"evidence.{k}" for k in ev if k not in EVIDENCE_FIELDS]
        body["evidence"] = {k: ev[k] for k in EVIDENCE_FIELDS if k in ev}
    return body, dropped


# --- finding the pull request ------------------------------------------------------

def url_ok(url, api: str) -> bool:
    """https always; http only when the API itself is http (the local fake in the tests)."""
    if not isinstance(url, str):
        return False
    return url.startswith("https://") or (api.lower().startswith("http://")
                                          and url.startswith("http://"))


def check_shape(pr, api: str) -> Optional[str]:
    """None if the pull request entry has the shape we rely on, else the reason it is refused."""
    if not isinstance(pr, dict):
        return "entry is not an object"
    number = pr.get("number")
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        return f"bad number {str(number)[:20]!r}"
    head = pr.get("head")
    sha = head.get("sha") if isinstance(head, dict) else None
    if not isinstance(sha, str) or not SHA_RE.match(sha):
        return "bad head sha"
    if not url_ok(pr.get("html_url"), api):
        return "bad html_url"
    if not isinstance(pr.get("title", ""), str):
        return "bad title"
    user = pr.get("user")
    if user is not None and not (isinstance(user, dict)
                                 and isinstance(user.get("login", ""), str)):
        return "bad user"
    created = pr.get("created_at", "")
    if created is not None and not isinstance(created, str):
        return "bad created_at"
    if created:
        try:
            parse_time(created)
        except ValueError:
            return "bad created_at"
    return None


def author_of(pr: dict) -> str:
    return ((pr.get("user") or {}).get("login") or "")


def head_repo_of(pr: dict) -> str:
    repo = (pr.get("head") or {}).get("repo")
    name = repo.get("full_name") if isinstance(repo, dict) else None
    return name if isinstance(name, str) else ""


def check_origin(pr: dict, repo: str, args) -> Optional[str]:
    """None if the pull request may be used, else the reason it is refused."""
    who = author_of(pr)
    head_repo = head_repo_of(pr)
    if not args.allow_forks and head_repo.lower() != repo.lower():
        return (f"its head branch is in {head_repo or 'an unknown repository'!r}, "
                f"not in {repo} (author {who!r})")
    if args.authors and who.lower() not in args.authors:
        return f"author {who!r} is not in the allowed authors"
    return None


def candidates(gh: GitHub, args, since: datetime, api: str, reported: set) -> list[dict]:
    """Usable open pull requests, newest first. Each refusal is logged once."""
    def refuse(key: str, msg: str) -> None:
        if key not in reported:
            reported.add(key)
            say(msg)

    if args.pr is not None:
        pr = gh.pull(args.pr)
        if pr is None:
            return []
        bad = check_shape(pr, api)
        if bad or pr["number"] != args.pr:
            raise WatchError(f"GitHub returned a malformed answer for PR #{args.pr}: "
                             f"{bad or 'wrong number'}", 5)
        why = check_origin(pr, gh.repo, args)
        if why:
            raise WatchError(f"refusing PR #{args.pr}: {why}"
                             + ("" if args.allow_forks else " (--allow-forks overrides the "
                                                           "branch check)"), 2)
        return [pr]
    out = []
    for i, pr in enumerate(gh.open_pulls()):
        bad = check_shape(pr, api)
        if bad or not pr.get("created_at"):
            refuse(f"bad{i}:{bad}", f"skipped a malformed pull request entry: "
                                    f"{bad or 'no created_at'}")
            continue
        if parse_time(pr["created_at"]) < since:
            continue
        why = check_origin(pr, gh.repo, args)
        if why:
            refuse(f"pr{pr['number']}", f"skipped PR #{pr['number']} by {author_of(pr)!r}: {why}")
            continue
        out.append(pr)
    out.sort(key=lambda p: p["created_at"], reverse=True)
    return out


def look_for_verdict(gh: GitHub, args, pr: dict):
    """Returns (verdict_path, raw_bytes, parsed dict, files) or None if not usable yet."""
    number = pr["number"]
    sha = pr["head"]["sha"]
    files = gh.files(number)
    path = find_verdict_path(files)
    if path is None:
        return None
    try:
        raw = gh.file_at(path, sha)
    except WatchError as e:
        if e.code == 6:
            warn(f"PR #{number}: {path} is larger than {MAX_FILE_BYTES // 1024} KB, refused")
            return None
        raise
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        say(f"PR #{number}: {path} is not valid JSON yet")
        return None
    if not isinstance(data, dict):
        say(f"PR #{number}: {path} is not a JSON object yet")
        return None
    if args.incident and data.get("incident_id") != args.incident:
        say(f"PR #{number}: verdict is for another incident, ignored")
        return None
    return path, raw, data, files


# --- saving artifacts ------------------------------------------------------------

def save_artifacts(gh: GitHub, pr: dict, raw: bytes, files: list, state: Path, root: Path) -> dict:
    number, sha = pr["number"], pr["head"]["sha"]
    pr_dir = state / str(number)
    if pr_dir.exists():
        shutil.rmtree(pr_dir)
    (pr_dir / "rules").mkdir(parents=True)
    (pr_dir / "verdict.json").write_bytes(raw)

    listing = gh.list_dir("rules", sha) or []
    saved = 0
    for item in listing if isinstance(listing, list) else []:
        name = item.get("name") if isinstance(item, dict) else None
        if not isinstance(name, str) or not name.endswith(".sql") or item.get("type") != "file":
            continue
        if not RULE_NAME.match(name) or name in (".", ".."):
            warn(f"refused rule file name {name!r}")
            continue
        size = item.get("size")
        if isinstance(size, int) and size > MAX_FILE_BYTES:
            warn(f"refused rules/{name}: larger than {MAX_FILE_BYTES // 1024} KB")
            continue
        try:
            body = gh.file_at(f"rules/{name}", sha)
        except WatchError as e:
            if e.code == 6:
                warn(f"refused rules/{name}: larger than {MAX_FILE_BYTES // 1024} KB")
                continue
            raise
        if body is None:
            continue
        (pr_dir / "rules" / name).write_bytes(body)
        saved += 1

    report = find_report_path(files)
    if report:
        try:
            body = gh.file_at(report, sha)
        except WatchError as e:
            if e.code != 6:
                raise
            warn(f"refused {report}: larger than {MAX_FILE_BYTES // 1024} KB")
            body = None
        if body is not None:
            (pr_dir / "report.md").write_bytes(body)

    pr_json = {"number": number, "url": pr["html_url"], "title": pr.get("title", ""),
               "head_sha": sha, "author": author_of(pr), "head_repo": head_repo_of(pr),
               "created_at": pr.get("created_at", "")}
    (pr_dir / "pr.json").write_text(json.dumps(pr_json, indent=2) + "\n")
    say(f"saved artifacts in {rel(pr_dir, root)} ({saved} rule files"
        f"{', report.md' if (pr_dir / 'report.md').exists() else ''})")
    return pr_json


def rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def write_latest(state: Path, root: Path, pr_json: dict, applied: bool,
                 name: str = "latest") -> None:
    """Write the pointer file .state/pr/<name>.json (latest.json, or rehearsal.json)."""
    d = state / str(pr_json["number"])
    latest = {"number": pr_json["number"], "url": pr_json["url"], "head_sha": pr_json["head_sha"],
              "dir": rel(d, root), "verdict_file": rel(d / "verdict.json", root),
              "rules_dir": rel(d / "rules", root), "applied": applied, "fetched_at": iso(now_utc())}
    tmp = state / f"{name}.json.tmp"
    tmp.write_text(json.dumps(latest, indent=2) + "\n")
    os.replace(tmp, state / f"{name}.json")


# --- applying the verdict -----------------------------------------------------------

def apply_verdict(data: dict, pr_url: str) -> str:
    """POST to the breaker. Returns a one-line summary; raises WatchError(4)."""
    body, dropped = clean_verdict(data)
    if dropped:
        say("dropped unknown fields from the verdict: " + ", ".join(dropped))
    if body.get("pr_url") != pr_url:
        if body.get("pr_url"):
            say(f"pr_url in the file ({body['pr_url']!r}) differs from the real one, using {pr_url}")
        body["pr_url"] = pr_url
    incident_id = body.get("incident_id")
    if not isinstance(incident_id, str) or not INCIDENT_ID.match(incident_id):
        raise WatchError(f"invalid incident_id in verdict.json: {str(incident_id)[:40]!r}", 4)
    base = (os.environ.get("BREAKER_URL") or "http://127.0.0.1:8000").rstrip("/")
    token = os.environ.get("ADMIN_TOKEN") or "dev-admin-token"
    say(f"applying verdict {body.get('verdict')!r} to {incident_id} at {base}")
    try:
        r = httpx.post(f"{base}/incidents/{incident_id}/verdict", json=body, timeout=60,
                       headers={"X-Admin-Token": token}, trust_env=not is_loopback(base))
    except httpx.HTTPError as e:
        raise WatchError(f"could not reach the breaker at {base}: {type(e).__name__}", 4)
    if r.status_code // 100 != 2:
        raise WatchError(f"breaker answered HTTP {r.status_code}: {r.text[:300]}", 4)
    try:
        out = r.json()
        return (f"verdict {out.get('verdict')} status {out.get('status')} key "
                f"{out.get('key_state')} held {out.get('held_count')}")
    except ValueError:
        return r.text[:200]


# --- main ----------------------------------------------------------------------------

def run(args) -> int:
    root = Path(args.repo_root).resolve()
    state = root / ".state" / "pr"
    repo = args.repo or os.environ.get("GITHUB_REPO", "").strip() or detect_repo(root)
    if not repo or not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", repo):
        raise WatchError("no repository: use --repo OWNER/NAME or set GITHUB_REPO", 2)
    token = find_token()
    if not token:
        warn("no GITHUB_TOKEN and no gh login: the limit is 60 GitHub requests per hour")
    since = parse_time(args.since) if args.since else now_utc()
    deadline = time.monotonic() + args.timeout_min * 60
    api = os.environ.get("GITHUB_API_URL", "").strip() or "https://api.github.com"
    gh = GitHub(api, repo, token, deadline)
    reported: set = set()
    what = f"PR #{args.pr}" if args.pr is not None else f"open PRs since {iso(since)}"
    say(f"watching {repo} for {what}")
    try:
        waiting_said = False
        while True:
            found = None
            for pr in candidates(gh, args, since, api, reported):
                if f"found{pr['number']}" not in reported:
                    reported.add(f"found{pr['number']}")
                    say(f"found PR #{pr['number']} by {author_of(pr)!r} "
                        f"from {head_repo_of(pr) or '?'}: {str(pr.get('title', ''))[:80]!r}")
                res = look_for_verdict(gh, args, pr)
                if res:
                    found = (pr, res)
                    break
            if found:
                break
            if time.monotonic() >= deadline:
                raise WatchError(f"timeout after {args.timeout_min} min: no usable verdict.json", 3)
            if not waiting_said:
                say("waiting for verdict.json")
                waiting_said = True
            time.sleep(args.interval)

        pr, (vpath, raw, data, files) = found
        say(f"PR #{pr['number']} has {vpath} at {pr['head']['sha'][:7]}")
        pr_json = save_artifacts(gh, pr, raw, files, state, root)
    finally:
        gh.close()

    write_latest(state, root, pr_json, applied=False, name=args.save_as)
    if args.save_as != "latest":
        say(f"pointer written to {rel(state / (args.save_as + '.json'), root)}")
    if args.no_apply:
        return finish(f"saved PR #{pr_json['number']} (not applied, --no-apply)")
    summary = apply_verdict(data, pr_json["url"])
    write_latest(state, root, pr_json, applied=True, name=args.save_as)
    say(f"breaker: {summary}")
    return finish(f"applied verdict of PR #{pr_json['number']} ({pr_json['url']}): {summary}")


def finish(msg: str) -> int:
    print(msg if QUIET else f"[{now_utc().strftime('%H:%M:%S')}] done: {msg}", flush=True)
    return 0


def main(argv: list[str]) -> int:
    global QUIET
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--repo", help="OWNER/NAME (default: $GITHUB_REPO, else git remote origin)")
    p.add_argument("--since", help="ISO 8601 UTC; only PRs created at or after it "
                                   "(default: now)")
    p.add_argument("--pr", type=int, help="watch this PR number, ignore --since")
    p.add_argument("--authors", default=os.environ.get("WATCH_PR_AUTHORS", ""),
                   help="comma separated GitHub logins allowed to open the PR "
                        "(default: $WATCH_PR_AUTHORS, else any author with a branch in the repo)")
    p.add_argument("--allow-forks", action="store_true",
                   help="accept pull requests whose head branch is in another repository")
    p.add_argument("--incident", help="ignore verdict files for other incidents")
    p.add_argument("--interval", type=float, default=10, help="seconds between polls")
    p.add_argument("--timeout-min", type=float, default=30, help="give up after this (exit 3)")
    p.add_argument("--no-apply", action="store_true", help="save artifacts only")
    p.add_argument("--quiet", action="store_true", help="print only the final line")
    p.add_argument("--save-as", choices=POINTER_NAMES, default="latest",
                   help="pointer file to write in .state/pr/: latest.json (default) or "
                        "rehearsal.json, the fallback that scripts/demo.py uses on stage")
    p.add_argument("--repo-root", default=str(ROOT), help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    QUIET = args.quiet
    args.authors = {a.strip().lower() for a in args.authors.split(",") if a.strip()}
    if args.since:
        try:
            parse_time(args.since)
        except ValueError:
            p.error("--since must be ISO 8601, for example 2026-11-21T02:00:00Z")
    if args.incident and not INCIDENT_ID.match(args.incident):
        p.error("--incident must match ^[A-Za-z0-9_]+$")
    try:
        return run(args)
    except WatchError as e:
        print(f"error: {e}", file=sys.stderr, flush=True)
        return 5 if e.code == 6 else e.code
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
