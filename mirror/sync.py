"""Create a mirror repository, or bring one up to date with its source.

    MIRROR_TOKEN=... python3 -m mirror.sync SOURCE REPO --seed     # cc0-mirror: create it and give it what it needs
    python3 -m mirror.sync SOURCE REPO --self DIR                  # the repository's own update workflow
    python3 -m mirror.sync SOURCE REPO --dry-run [--workdir DIR]   # try it locally; touches nothing on GitHub

--seed creates the repository in the zuujis-cc0 organisation if it is missing and pushes its workflows (update and
check), LICENSE and a README saying it is filling itself; only cc0-mirror's token can do that, because a repository's
own token may not create repositories or write workflows. Everything else happens in the repository itself, in its
update workflow (--self): it downloads every file the plan (cc0-mirror's plan/SOURCE.json) gives it, each checked
against the source's size (and MD5 where the source gives one), unpacks ZIPs into folders, splits glTF buffers too big
for git, removes what no longer belongs, writes previews/, README.md, MANIFEST.tsv (path, bytes, SHA-256, MD5,
source) and .mirror.json (the assets and their hashes, read by plan.py), checks the result and pushes. An asset that
has not changed since the last run is carried over without downloading it again.
"""
import argparse
import concurrent.futures
import datetime
import hashlib
import io
import json
import os
import posixpath
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import zipfile
from pathlib import Path

from mirror import gltf_split
from mirror.common import FILE_LIMIT, HUB, ORG, ROOT, get, gh, load_plan, load_source, size_label

TEMPLATE = ROOT / "mirror" / "template"
GENERATED = {"README.md", "MANIFEST.tsv", ".mirror.json", "LICENSE", ".github/workflows/check.yml",
             ".github/workflows/update.yml", ".github/verify.py"}
# Bump when a workflow template changes: cc0-mirror then re-seeds every repository (a repository's own token may not
# change its workflows, so --self leaves .github/workflows/ alone; other template files it updates itself).
TEMPLATE_VERSION = 2
BOT = ("github-actions[bot]", "41898282+github-actions[bot]@users.noreply.github.com")
# Files a ZIP may hold that are not part of the asset.
JUNK = ("__MACOSX/", ".DS_Store", "Thumbs.db", "desktop.ini")


def run(*args, cwd=None, check=True):
    return subprocess.run(args, cwd=cwd, check=check, capture_output=True, text=True)


def digest(path, algo):
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def kind_title(src, info):
    return getattr(src, "KIND_TITLES", {}).get(info["kind"], info["kind"])


def ensure_repo(src, repo, info):
    status, _ = gh("GET", f"repos/{ORG}/{repo}")
    if status == 200:
        return True
    status, body = gh("POST", f"orgs/{ORG}/repos", {
        "name": repo,
        "description": f"{src.TITLE}: " + " ".join(x for x in (info["category"], kind_title(src, info).lower()) if x) + " (CC0), kept here for game makers. "
                       f"Part of zuujis-cc0.",
        "homepage": src.SITE,
        "has_issues": False, "has_projects": False, "has_wiki": False, "has_discussions": False,
        "visibility": "public",
    })
    if status != 201:
        sys.exit(f"creating {repo}: {status} {body}")
    gh("PUT", f"repos/{ORG}/{repo}/topics", {"names": ["cc0", "public-domain", "game-assets", src.NAME, info["kind"]]})
    gh("PATCH", f"repos/{ORG}/{repo}", {"security_and_analysis": {
        "secret_scanning": {"status": "enabled"}, "secret_scanning_push_protection": {"status": "enabled"}}})
    return False


def cron_for(repo):
    """A weekly time of its own for each repository, so they do not all ask the source at once."""
    h = int(hashlib.sha256(repo.encode()).hexdigest(), 16)
    return f"{h % 60} {(h >> 8) % 24} * * {(h >> 16) % 7}"


def render_update(src, repo):
    text = (TEMPLATE / ".github/workflows/update.yml").read_text()
    for key, value in (("@CRON@", cron_for(repo)), ("@SOURCE@", src.NAME), ("@REPO@", repo), ("@TITLE@", src.TITLE)):
        text = text.replace(key, value)
    return text


def header(src, info, count):
    return [
        f"# {src.TITLE}: " + " ".join(x for x in (info["category"], kind_title(src, info)) if x),
        "",
        f"**{count} assets from [{src.TITLE}]({src.SITE}), dedicated to the public domain under "
        f"[CC0]({src.LICENSE_URL}).** Use them in any game, free or paid, and change them however you like; no credit is needed.",
        "",
    ]


def footer():
    return ["", "---", "",
            f"Kept up to date by this repository's own [update workflow](.github/workflows/update.yml), with code and plan "
            f"from [cc0-mirror](https://github.com/{ORG}/cc0-mirror). Something wrong? "
            f"[Open an issue there](https://github.com/{ORG}/cc0-mirror/issues/new) or use the "
            f"[private report form](https://games.zuujis.com/report).", ""]


def readme(src, info, assets, metas, total, skipped):
    lines = header(src, info, len(assets)) + [
        f"{src.ABOUT[info['kind']]} {size_label(total)} in all.",
        "",
        f"This is a copy kept by [zuujis-cc0](https://github.com/{ORG}) so game makers can take single files from GitHub. "
        f"It is not made by or affiliated with {src.TITLE}: the originals, often in more formats and higher resolutions, "
        f"are on [{src.SITE.split('//')[1]}]({src.SITE}). If they help you, consider [supporting {src.TITLE}]({src.SUPPORT_URL}).",
        "",
        f"Every file is listed in [MANIFEST.tsv](MANIFEST.tsv) with its size and SHA-256. More free assets: [the collection]({HUB}#readme).",
        "",
        "| | | | |",
        "| :-: | :-: | :-: | :-: |",
    ]
    cells = []
    for aid in assets:
        m = metas[aid]
        by = ", ".join(m.get("authors") or []) or src.TITLE
        picture = (f'<a href="{aid}"><img src="previews/{aid}.{m.get("thumb_ext", "webp")}" width="160" alt="{m["name"]}"></a><br>'
                   if m.get("thumb") else "")
        cells.append(f'{picture}<b><a href="{aid}">{m["name"]}</a></b><br><sub>by {by} · <a href="{m["page"]}">source</a></sub>')
    for i in range(0, len(cells), 4):
        row = cells[i:i + 4] + [""] * (4 - len(cells[i:i + 4]))
        lines.append("| " + " | ".join(row) + " |")
    if skipped:
        lines += ["", "### Not copied here", "",
                  f"These files are over GitHub's 100 MB limit; get them from {src.TITLE}:", ""]
        lines += [f"- `{p}` ({size_label(n)}), from [{metas[a]['name']}]({metas[a]['page']})" for a, p, n in sorted(skipped)]
    return "\n".join(lines + footer())


def seed_readme(src, info, count):
    return "\n".join(header(src, info, count) + [
        "**This repository is filling itself.** Its [update workflow](.github/workflows/update.yml) is downloading "
        f"the files from [{src.TITLE}]({src.SITE}); the list of assets with pictures appears here when it is done.",
    ] + footer())


def safe_member(name):
    """A ZIP member's path inside the asset folder, or None for folders, junk and anything that would escape."""
    name = name.replace("\\", "/")
    if name.endswith("/") or any(j in name for j in JUNK):
        return None
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts or ".." in parts or parts[0].endswith(":"):
        return None
    return "/".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("repo")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--seed", action="store_true", help="create the repository and push what it needs to fill itself")
    mode.add_argument("--self", type=Path, dest="self_dir", help="update this checkout in place and push (update workflow)")
    mode.add_argument("--dry-run", action="store_true", help="fill a fresh copy in --workdir; push nothing")
    ap.add_argument("--workdir", type=Path, default=ROOT / "work")
    opts = ap.parse_args()
    src = load_source(opts.source)
    plan = load_plan(opts.source)
    info = plan["repos"][opts.repo]
    entries = {a: v for a, v in plan["assets"].items() if v["repo"] == opts.repo}
    assets = sorted(entries)
    metas = {a: entries[a]["meta"] for a in assets}
    token = os.environ.get("MIRROR_TOKEN", "")

    if opts.self_dir:
        work = opts.self_dir.resolve()
        if not (work / ".git").exists():
            sys.exit(f"{work} is not a checkout")
    else:
        if opts.seed and not token:
            sys.exit("MIRROR_TOKEN is not set")
        exists = ensure_repo(src, opts.repo, info) if opts.seed else False
        work = opts.workdir / opts.repo
        if work.exists():
            shutil.rmtree(work)
        work.parent.mkdir(parents=True, exist_ok=True)
        remote = f"https://x-access-token:{token}@github.com/{ORG}/{opts.repo}.git"
        cloned = exists and run("git", "clone", "-q", "--depth", "1", remote, str(work), check=False).returncode == 0
        if not cloned:
            if work.exists():
                shutil.rmtree(work)
            work.mkdir()
            run("git", "init", "-q", "-b", "main", cwd=work)
            run("git", "remote", "add", "origin", remote, cwd=work)
    # The runner's (or a Mac's) global ignore rules must not drop files such as folders named "icons".
    run("git", "config", "core.excludesFile", "/dev/null", cwd=work)

    old_state = json.loads((work / ".mirror.json").read_text()) if (work / ".mirror.json").exists() else {}
    old_assets, old_template = old_state.get("assets", {}), old_state.get("template", 0)
    old_rows = {}
    if (work / "MANIFEST.tsv").exists():
        for line in (work / "MANIFEST.tsv").read_text().splitlines()[1:]:
            path, size, sha, md5, source = line.split("\t")
            old_rows[path] = {"size": int(size), "sha256": sha, "md5": md5, "url": source}

    def write_template(workflows):
        for t in ("LICENSE", ".github/verify.py", ".github/workflows/check.yml", ".github/workflows/update.yml"):
            if t.startswith(".github/workflows/") and not workflows:
                continue
            (work / t).parent.mkdir(parents=True, exist_ok=True)
            if t.endswith("update.yml"):
                (work / t).write_text(render_update(src, opts.repo))
            else:
                shutil.copyfile(TEMPLATE / t, work / t)

    if opts.seed:
        # Only what the repository needs to fill and update itself; what it already holds stays as it is.
        write_template(workflows=True)
        if not old_state:
            (work / "README.md").write_text(seed_readme(src, info, len(assets)))
            (work / "MANIFEST.tsv").write_text("path\tbytes\tsha256\tmd5\tsource\n")
        state = dict(old_state or {"source": src.NAME, "site": src.SITE, "license": src.LICENSE_URL, "assets": {}})
        state["template"] = TEMPLATE_VERSION
        (work / ".mirror.json").write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")
        commit_and_push(work, opts, token, f"Set up the update workflow ({src.TITLE}, {len(assets)} assets to fill)")
        return

    # What is there already: an asset whose hash is unchanged and whose files are all still there (with their sizes)
    # is carried over as it is, without downloading or hashing it again.
    def carried(aid):
        rows = {p: r for p, r in old_rows.items() if p.startswith(f"{aid}/")}
        if old_assets.get(aid) != entries[aid]["hash"] or not rows:
            return None
        if all((work / p).is_file() and (work / p).stat().st_size == r["size"] for p, r in rows.items()):
            return rows
        return None

    final, todo = {}, []
    for aid in assets:
        rows = carried(aid)
        if rows:
            final.update(rows)
        else:
            todo.append(aid)
    threads = getattr(src, "THREADS", 4)
    with concurrent.futures.ThreadPoolExecutor(threads) as pool:
        lists = dict(zip(todo, pool.map(lambda a: src.files(a, metas[a]), todo)))
    # An asset being replaced loses its old files first, so nothing stale is left inside its folder.
    for aid in todo:
        if (work / aid).is_dir():
            shutil.rmtree(work / aid)

    skipped = []  # (asset, path, bytes): files over git's limit, listed in the README instead
    added = {}  # path -> size, md5, url

    def fetch(job):
        aid, f = job
        data = get(f["url"], timeout=900)
        if len(data) != f["size"] or (f.get("md5") and hashlib.md5(data).hexdigest() != f["md5"]):
            raise SystemExit(f"{f['url']}: {len(data)} bytes; the source listed {f['size']} (or the MD5 differs)")
        out = {}
        kind = f.get("unzip")
        if kind == "7z" and not shutil.which("7z"):
            kind = None  # no 7z tool here: keep the archive as it is
            f = dict(f, path=f["path"] + posixpath.splitext(urllib.parse.urlsplit(f["url"]).path)[1])
        if kind == "7z":
            with tempfile.TemporaryDirectory() as tmp:
                arc = Path(tmp) / ("a" + posixpath.splitext(urllib.parse.urlsplit(f["url"]).path)[1])
                arc.write_bytes(data)
                unpacked = Path(tmp) / "x"
                if run("7z", "x", "-y", f"-o{unpacked}", str(arc), check=False).returncode:
                    raise SystemExit(f"{f['url']}: 7z could not unpack it")
                for member in sorted(unpacked.rglob("*")):
                    if not member.is_file() or member.is_symlink():
                        continue
                    rel = safe_member(member.relative_to(unpacked).as_posix())
                    if not rel:
                        continue
                    path = posixpath.join(f["path"], rel)
                    size = member.stat().st_size
                    if size > FILE_LIMIT:
                        skipped.append((aid, path, size))
                        continue
                    dest = work / path
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(member), dest)
                    out[path] = {"size": size, "md5": "", "url": f"{f['url']} → {rel}"}
        elif kind:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for member in z.infolist():
                    rel = safe_member(member.filename)
                    if not rel:
                        continue
                    path = posixpath.join(f["path"], rel)
                    if member.file_size > FILE_LIMIT:
                        skipped.append((aid, path, member.file_size))
                        continue
                    dest = work / path
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(z.read(member))
                    out[path] = {"size": member.file_size, "md5": "", "url": f"{f['url']} → {rel}"}
        else:
            dest = work / f["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            if f["size"] > FILE_LIMIT and not f.get("splittable"):
                skipped.append((aid, f["path"], f["size"]))
                return out
            dest.write_bytes(data)
            out[f["path"]] = {"size": f["size"], "md5": f.get("md5", ""), "url": f["url"]}
        return out

    def preview(aid):
        m = metas[aid]
        dest = work / "previews" / f"{aid}.{m.get('thumb_ext', 'webp')}"
        if dest.exists() and old_assets.get(aid) == entries[aid]["hash"]:
            return
        if not m.get("thumb"):
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(get(m["thumb"]))

    jobs = [(a, f) for a in todo for f in lists[a]]
    with concurrent.futures.ThreadPoolExecutor(threads) as pool:
        for out in pool.map(fetch, jobs):
            added.update(out)
        list(pool.map(preview, assets))
    fetched = sum(f["size"] for _, f in jobs)

    # glTF models whose .bin is over git's limit: split the .bin into parts and point the .gltf at them.
    for path in sorted(p for p in list(added) if p.endswith(".gltf")):
        gltf = work / path
        if not gltf_split.needs_split(gltf, FILE_LIMIT):
            continue
        removed, parts = gltf_split.split(gltf, FILE_LIMIT)
        folder = posixpath.dirname(path)
        for rel in removed:
            orig = added.pop(posixpath.normpath(f"{folder}/{rel}"))
            for part in parts:
                added[posixpath.normpath(f"{folder}/{part}")] = {
                    "size": (work / folder / part).stat().st_size, "md5": "", "url": orig["url"] + " (split into parts)"}
        added[path] = dict(added[path], size=gltf.stat().st_size, md5="", url=added[path]["url"] + " (buffers split)")
        print(f"{path}: split {', '.join(removed)} into {len(parts)} parts")
    for path, f in added.items():
        final[path] = {"size": f["size"], "sha256": digest(work / path, "sha256"), "md5": f["md5"], "url": f["url"]}

    keep = set(final) | GENERATED | {f"previews/{a}.{metas[a].get('thumb_ext', 'webp')}" for a in assets if metas[a].get("thumb")}
    tracked = run("git", "ls-files", "-z", cwd=work).stdout.split("\0")
    on_disk = {p.relative_to(work).as_posix() for p in work.rglob("*") if p.is_file() and p.relative_to(work).parts[0] != ".git"}
    for rel in (set(filter(None, tracked)) | on_disk) - keep:
        (work / rel).unlink(missing_ok=True)

    total = sum(f["size"] for f in final.values())
    rows = ["path\tbytes\tsha256\tmd5\tsource"]
    rows += [f"{p}\t{f['size']}\t{f['sha256']}\t{f['md5']}\t{f['url']}" for p, f in sorted(final.items())]
    (work / "MANIFEST.tsv").write_text("\n".join(rows) + "\n")
    (work / "README.md").write_text(readme(src, info, assets, metas, total, skipped))
    (work / ".mirror.json").write_text(json.dumps({
        "source": src.NAME, "site": src.SITE, "license": src.LICENSE_URL,
        "assets": {a: entries[a]["hash"] for a in assets},
        "template": TEMPLATE_VERSION if opts.dry_run else old_template,
    }, indent=1, sort_keys=True) + "\n")
    write_template(workflows=opts.dry_run)
    if opts.dry_run:
        (work / ".github/workflows").mkdir(parents=True, exist_ok=True)

    check = run("python3", ".github/verify.py", cwd=work, check=False)
    if check.returncode:
        sys.exit(f"{opts.repo}: the check failed:\n{check.stdout}{check.stderr}")
    when = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    msg = f"Mirror {src.TITLE}: {len(assets)} assets, {size_label(total)} ({when})"
    commit_and_push(work, opts, token, msg, extra=f"downloaded {size_label(fetched)}")


def commit_and_push(work, opts, token, msg, extra=""):
    run("git", "add", "-A", cwd=work)
    if not run("git", "status", "--porcelain", cwd=work).stdout.strip():
        print(f"{opts.repo}: up to date")
        return
    run("git", "-c", f"user.name={BOT[0]}", "-c", f"user.email={BOT[1]}", "commit", "-q", "-m", msg, cwd=work)
    if opts.dry_run:
        print(f"{opts.repo}: dry run, would push: {msg}; {extra} into {work}")
        return
    push = run("git", "push", "-q", "origin", "HEAD:main", cwd=work, check=False)
    if push.returncode:
        err = push.stderr.replace(token, "***") if token else push.stderr
        sys.exit(f"pushing {opts.repo} failed: {err}")
    print(f"{opts.repo}: pushed: {msg}{'; ' + extra if extra else ''}")


if __name__ == "__main__":
    main()
