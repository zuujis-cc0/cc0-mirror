"""OpenGameArt (https://opengameart.org): every entry whose licences include CC0, as its uploader published it.

OpenGameArt has no API, and its robots.txt asks crawlers for 10 seconds between requests, which common.SPACING
enforces for every request to the site (pages and files alike, across threads). So the catalogue is built a little
at a time: each plan run reads the listing (newest first) and the pages of entries it has not seen, within BUDGET
seconds, and remembers where it stopped (plan["state"]). Kenney's and Quaternius's own uploads are read first, from
their user pages, and get repositories of their own. Entries are not re-read once known (OpenGameArt entries rarely
change); a changed one can be refreshed by removing it from plan/opengameart.json.

Files are mirrored as uploaded, except ZIPs, which are unpacked into a folder (and 7z/RAR archives too where the
7z tool is installed, as on GitHub's runners). Only one OpenGameArt repository fills at a time (MAX_PARALLEL).
"""
import hashlib
import html
import os
import posixpath
import re
import sys
import time
import urllib.parse

from mirror.common import SPACING, get, slug

NAME = "opengameart"
TITLE = "OpenGameArt"
SITE = "https://opengameart.org"
LICENSE_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
SUPPORT_URL = "https://opengameart.org/content/faq"
SPACING["opengameart.org"] = 10
THREADS = 1
MAX_PARALLEL = 1
BUDGET = int(os.environ.get("OGA_BUDGET", 5 * 3600))
PRIORITY_USERS = {"kenney": "Kenney", "quaternius": "Quaternius"}
LISTING = f"{SITE}/art-search-advanced?field_art_licenses_tid%5B%5D=4&sort_by=created&sort_order=DESC&page={{}}"
KIND_TITLES = {"2d-art": "2D art", "3d-art": "3D art", "concept-art": "concept art", "texture": "textures",
               "music": "music", "sound-effect": "sound effects", "document": "documents", "other": "other"}
ABOUT = {k: f"{v[:1].upper()}{v[1:]} from OpenGameArt entries licensed CC0 (some also offer other licences; CC0 is the one that applies here)."
         for k, v in KIND_TITLES.items()}
ARCHIVES = {".zip": "zip", ".7z": "7z", ".rar": "7z"}


def _field(page, name):
    m = re.search(rf'field-name-{name}\b.*?<div class="field-items">(.*?)</div></div></div>', page, re.S)
    return m.group(1) if m else ""


def entry(aid):
    """Read one entry's page: None if it is not CC0 (or not there)."""
    try:
        page = get(f"{SITE}/content/{aid}").decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001 (a missing entry must not stop the catalogue)
        print(f"opengameart: {aid}: {e}", file=sys.stderr)
        return None
    if "CC0" not in re.sub(r"<[^>]+>", " ", _field(page, "field-art-licenses")):
        return None
    title = re.search(r'<meta property="og:title" content="([^"]*)"', page)
    user = re.search(r"field-name-author-submitter.*?<a href=\"/users/([^\"]+)\">([^<]*)</a>", page, re.S)
    kind_text = re.sub(r"<[^>]+>", "", _field(page, "field-art-type")).strip() or "Other"
    files = []
    for url, length in re.findall(r'<a href="(https://opengameart\.org/sites/default/files/[^"]+)" type="[^"]*; length=(\d+)"',
                                  _field(page, "field-art-files")):
        name = posixpath.basename(urllib.parse.unquote(urllib.parse.urlsplit(html.unescape(url)).path))
        files.append({"name": name, "url": html.unescape(url), "size": int(length)})
    thumb = re.search(r"field-name-field-art-preview.*?<img src='([^']+)'", page, re.S)
    if not files:
        return None
    uid, uname = (user.group(1), html.unescape(user.group(2))) if user else ("", "")
    kind = slug(kind_text)
    group = f"{uid}-{kind}" if uid in PRIORITY_USERS else kind
    thumb_url = html.unescape(thumb.group(1)) if thumb else ""
    ext = posixpath.splitext(urllib.parse.urlsplit(thumb_url).path)[1].lower().lstrip(".") or "png"
    return {
        "kind": kind if kind in KIND_TITLES else "other",
        "group": group,
        "category": PRIORITY_USERS.get(uid, ""),
        "name": html.unescape(title.group(1)) if title else aid,
        "authors": [uname] if uname else [],
        "hash": hashlib.sha1(repr(sorted((f["url"], f["size"]) for f in files)).encode()).hexdigest(),
        "page": f"{SITE}/content/{aid}",
        "thumb": thumb_url,
        "thumb_ext": ext if ext in ("png", "jpg", "jpeg", "gif", "webp") else "png",
        "files": files,
    }


def _ids(page_html):
    return list(dict.fromkeys(i for i in re.findall(r'<span class="art-preview-title"><a href="/content/([^"#?/]+)"', page_html)))


def catalogue(previous=None):
    """Known entries (from the plan) plus whatever new ones this run's budget allows."""
    previous = previous or {"assets": {}, "skipped": {}}
    state = previous.setdefault("state", {})
    known = set(previous["assets"]) | set(previous["skipped"]) | set(state.get("not_cc0", []))
    out = {aid: dict(a["meta"], hash=a["hash"]) for aid, a in previous["assets"].items() if "meta" in a}
    pending = list(dict.fromkeys(state.get("pending", [])))
    deadline = time.monotonic() + BUDGET
    not_cc0 = set(state.get("not_cc0", []))

    def take(ids):
        for aid in ids:
            if aid not in known and aid not in pending:
                pending.append(aid)

    def fetch_pending():
        while pending and time.monotonic() < deadline:
            aid = pending.pop(0)
            meta = entry(aid)
            known.add(aid)
            if meta:
                out[aid] = meta
            else:
                not_cc0.add(aid)

    # 1. Priority uploaders, from their user pages: the whole list once (resuming where a run stopped), then each
    #    run only the first page, for new uploads.
    for user in PRIORITY_USERS:
        done = state.get(f"user_done_{user}")
        page_no = 0 if done else state.get(f"user_page_{user}", 0)
        while time.monotonic() < deadline:
            page = get(f"{SITE}/users/{user}?page={page_no}").decode("utf-8", "replace")
            ids = [i for i in dict.fromkeys(re.findall(r'href="/content/([^"#?/]+)"', page)) if i != "faq"]
            take(ids)
            if done:
                break
            if not ids or f"page={page_no + 1}" not in page:
                state[f"user_done_{user}"] = True
                break
            page_no += 1
            state[f"user_page_{user}"] = page_no
    fetch_pending()
    # 2. New entries at the top of the CC0 listing, then the backfill where the last run stopped.
    page_no = 0
    while time.monotonic() < deadline and (state.get("backfill_page") or state.get("backfill_done")):
        ids = _ids(get(LISTING.format(page_no)).decode("utf-8", "replace"))
        fresh = [i for i in ids if i not in known and i not in pending]
        take(ids)
        fetch_pending()
        if not fresh or not ids:
            break
        page_no += 1
    page_no = state.get("backfill_page", 0)
    while time.monotonic() < deadline and not state.get("backfill_done"):
        ids = _ids(get(LISTING.format(page_no)).decode("utf-8", "replace"))
        if not ids:
            state["backfill_done"] = True
            break
        take(ids)
        fetch_pending()
        if not pending:
            page_no += 1
            state["backfill_page"] = page_no
    state["pending"] = pending
    state["not_cc0"] = sorted(not_cc0)
    print(f"opengameart: {len(out)} CC0 entries known, {len(pending)} waiting, backfill at page "
          f"{state.get('backfill_page', 0)}{' (done)' if state.get('backfill_done') else ''}", file=sys.stderr)
    return out


def repo_base(group):
    return f"opengameart-{slug(group)}"


def files(aid, meta):
    out = []
    for f in meta["files"]:
        name = f["name"].replace("/", "_") or "file"
        stem, ext = posixpath.splitext(name)
        unpack = ARCHIVES.get(ext.lower())
        if unpack:
            out.append({"path": f"{aid}/{stem or 'files'}", "url": f["url"], "size": f["size"], "unzip": unpack})
        else:
            out.append({"path": f"{aid}/{name}", "url": f["url"], "size": f["size"]})
    return out
