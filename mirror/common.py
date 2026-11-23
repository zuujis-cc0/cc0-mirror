"""Shared helpers: HTTP with our own User-Agent, GitHub's API, names and the plan file. Standard library only."""
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Who this runs for. On GitHub the workflows set MIRROR_ORG and MIRROR_REPO, so a copy of this repository in another
# account or organisation works there without changes; config.json is for local runs and for the optional extras.
CONFIG = json.loads((ROOT / "config.json").read_text()) if (ROOT / "config.json").exists() else {}
ORG = os.environ.get("MIRROR_ORG") or CONFIG.get("org") or ""
CODE_REPO = os.environ.get("MIRROR_REPO") or CONFIG.get("code_repo") or (f"{ORG}/cc0-mirror" if ORG else "")
# The extras (display name, a link to more assets, a private report form) belong to the account config.json was
# written for; a copy running elsewhere leaves them out until its own config.json names its own account.
_OWN = CONFIG.get("org") == ORG
NAME = (CONFIG.get("name") if _OWN else None) or ORG
MORE_ASSETS = CONFIG.get("more_assets") if _OWN else None
REPORT_URL = CONFIG.get("report_url") if _OWN else None
# Sources ask that automated clients name themselves (Poly Haven's API terms require it): this repository's address.
UA = f"{CODE_REPO.rsplit('/', 1)[-1] or 'cc0-mirror'} (+https://github.com/{CODE_REPO})"
# GitHub recommends repositories under 1 GB; a mirror repository is filled to at most this.
REPO_LIMIT = int(CONFIG.get("repo_limit", 900_000_000))
# git refuses files over 100 MiB; a bigger file is left out (an asset made only of one is skipped in the plan).
FILE_LIMIT = 100_000_000  # 95.4 MiB, under git's 100 MiB
# Seconds to leave between requests to a host, across all threads (a source sets its own, e.g. robots.txt Crawl-delay).
SPACING = {}
_last, _lock = {}, threading.Lock()


def _wait_turn(url):
    host = urllib.parse.urlsplit(url).hostname or ""
    gap = SPACING.get(host, 0)
    if not gap:
        return
    while True:
        with _lock:
            now = time.monotonic()
            ready = _last.get(host, 0) + gap
            if now >= ready:
                _last[host] = now
                return
        time.sleep(ready - now)


def get(url, tries=5, timeout=120):
    """The body of url, retrying network errors and 5xx/429 with backoff."""
    for attempt in range(tries):
        _wait_turn(url)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == tries - 1:
                raise
        time.sleep(min(60, 2 ** attempt * 3))


def get_json(url):
    return json.loads(get(url))


def gh(method, path, body=None, token=None, ok=(200, 201, 204)):
    """Call GitHub's REST API. Returns (status, parsed body or None)."""
    token = token or os.environ.get("MIRROR_TOKEN") or os.environ.get("GH_TOKEN")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": UA, "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"https://api.github.com/{path.lstrip('/')}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        if e.code in ok:
            return e.code, None
        return e.code, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw.decode(errors="replace"))


def need_org():
    if not ORG or "/" not in CODE_REPO:
        raise SystemExit("Set MIRROR_ORG and MIRROR_REPO (owner/name of this code), or \"org\" and \"code_repo\" in config.json")


def slug(text):
    text = text.lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def size_label(n):
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1000


def plan_path(source):
    return ROOT / "plan" / f"{source}.json"


def load_plan(source):
    p = plan_path(source)
    plan = json.loads(p.read_text()) if p.exists() else {}
    for key in ("repos", "assets", "skipped"):
        plan.setdefault(key, {})
    return plan


def save_plan(source, plan):
    plan["repos"] = dict(sorted(plan["repos"].items()))
    plan["assets"] = dict(sorted(plan["assets"].items()))
    plan["skipped"] = dict(sorted(plan["skipped"].items()))
    plan_path(source).write_text(json.dumps(plan, indent=1, ensure_ascii=False) + "\n")


def load_source(name):
    import importlib

    return importlib.import_module(f"mirror.sources.{name}")
