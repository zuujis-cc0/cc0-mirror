"""Bring one mirror repository up to date with its source.

    MIRROR_TOKEN=... python3 -m mirror.sync SOURCE REPO [--workdir DIR] [--dry-run]

Creates the repository in the zuujis-cc0 organisation if it is missing, clones it (shallow), downloads every file the
plan gives it (each checked against the source's size and MD5; a file already there with the right MD5 is kept),
removes what no longer belongs, writes previews/, README.md, MANIFEST.tsv (path, bytes, SHA-256, MD5, source URL),
.mirror.json (the assets and their source hashes, read by plan.py), LICENSE and the check workflow, then commits and
pushes if anything changed. --dry-run does everything in DIR but creates, pushes and changes nothing on GitHub.
"""
import argparse
import concurrent.futures
import datetime
import hashlib
import json
import os
import posixpath
import shutil
import subprocess
import sys
from pathlib import Path

from mirror import gltf_split
from mirror.common import FILE_LIMIT, HUB, ORG, ROOT, get, gh, load_plan, load_source, size_label

TEMPLATE = ROOT / "mirror" / "template"
GENERATED = {"README.md", "MANIFEST.tsv", ".mirror.json", "LICENSE", ".github/workflows/check.yml",
             ".github/workflows/update.yml", ".github/verify.py"}
# Bump when a template file changes: cc0-mirror's own run then rewrites those files (a repository's own token may not
# change its workflows, so --self leaves .github/workflows/ alone).
TEMPLATE_VERSION = 1
BOT = ("github-actions[bot]", "41898282+github-actions[bot]@users.noreply.github.com")


def run(*args, cwd=None, check=True):
    return subprocess.run(args, cwd=cwd, check=check, capture_output=True, text=True)


def md5_of(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_repo(src, repo, info, dry):
    status, _ = gh("GET", f"repos/{ORG}/{repo}")
    if status == 200 or dry:
        return status == 200
    kind_title = info["kind"].rstrip("s") if info["kind"] != "hdris" else "HDRI"
    status, body = gh("POST", f"orgs/{ORG}/repos", {
        "name": repo,
        "description": f"{src.TITLE} {info['category']} {info['kind']} (CC0), mirrored for game makers. Part of zuujis-cc0.",
        "homepage": src.SITE,
        "has_issues": False, "has_projects": False, "has_wiki": False, "has_discussions": False,
        "visibility": "public",
    })
    if status != 201:
        sys.exit(f"creating {repo}: {status} {body}")
    gh("PUT", f"repos/{ORG}/{repo}/topics",
       {"names": ["cc0", "public-domain", "game-assets", src.NAME, info["kind"], kind_title.lower()][:20]})
    gh("PATCH", f"repos/{ORG}/{repo}", {"security_and_analysis": {
        "secret_scanning": {"status": "enabled"}, "secret_scanning_push_protection": {"status": "enabled"}}})
    return False


def readme(src, repo, info, assets, cat, total):
    lines = [
        f"# {src.TITLE}: {info['category']} {info['kind'] if info['kind'] != 'hdris' else 'HDRIs'}",
        "",
        f"**{len(assets)} assets from [{src.TITLE}]({src.SITE}), dedicated to the public domain under "
        f"[CC0]({src.LICENSE_URL}).** Use them in any game, free or paid, and change them however you like; no credit is needed.",
        "",
        f"{src.ABOUT[info['kind']]} {size_label(total)} in all.",
        "",
        f"This is a copy kept by [zuujis-cc0](https://github.com/{ORG}) so game makers can take single files from GitHub. "
        f"It is not made by or affiliated with {src.TITLE}: the originals, in more formats and higher resolutions, are on "
        f"[{src.SITE.split('//')[1]}]({src.SITE}). If they help you, consider [supporting {src.TITLE}]({src.SUPPORT_URL}).",
        "",
        f"Every file is listed in [MANIFEST.tsv](MANIFEST.tsv) with its size and SHA-256. More free assets: [the collection]({HUB}#readme).",
        "",
        "| | | | |",
        "| :-: | :-: | :-: | :-: |",
    ]
    cells = []
    for aid in assets:
        m = cat[aid]
        by = ", ".join(m["authors"]) or src.TITLE
        cells.append(f'<a href="{aid}"><img src="previews/{aid}.webp" width="160" alt="{m["name"]}"></a><br>'
                     f'<b><a href="{aid}">{m["name"]}</a></b><br><sub>by {by} · <a href="{m["page"]}">source</a></sub>')
    for i in range(0, len(cells), 4):
        row = cells[i:i + 4] + [""] * (4 - len(cells[i:i + 4]))
        lines.append("| " + " | ".join(row) + " |")
    lines += ["", "---", "",
              f"Updated automatically by [cc0-mirror](https://github.com/{ORG}/cc0-mirror). "
              f"Something wrong? [Open an issue there](https://github.com/{ORG}/cc0-mirror/issues/new) or use the "
              f"[private report form](https://games.zuujis.com/report).", ""]
    return "\n".join(lines)


def cron_for(repo):
    """A weekly time of its own for each repository, so they do not all ask the source at once."""
    h = int(hashlib.sha256(repo.encode()).hexdigest(), 16)
    return f"{h % 60} {(h >> 8) % 24} * * {(h >> 16) % 7}"


def render_update(src, repo):
    text = (TEMPLATE / ".github/workflows/update.yml").read_text()
    for key, value in (("@CRON@", cron_for(repo)), ("@SOURCE@", src.NAME), ("@REPO@", repo), ("@TITLE@", src.TITLE)):
        text = text.replace(key, value)
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("repo")
    ap.add_argument("--workdir", type=Path, default=ROOT / "work")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self", type=Path, dest="self_dir",
                    help="update this checkout of the repository in place and push with its own credentials "
                         "(the repository's update workflow)")
    opts = ap.parse_args()
    src = load_source(opts.source)
    plan = load_plan(opts.source)
    info = plan["repos"][opts.repo]
    assets = sorted(a for a, v in plan["assets"].items() if v["repo"] == opts.repo)
    cat = src.catalogue()
    assets = [a for a in assets if a in cat]
    token = os.environ.get("MIRROR_TOKEN", "")
    if opts.self_dir:
        work = opts.self_dir.resolve()
        if not (work / ".git").exists():
            sys.exit(f"{work} is not a checkout")
    else:
        if not opts.dry_run and not token:
            sys.exit("MIRROR_TOKEN is not set")
        exists = ensure_repo(src, opts.repo, info, opts.dry_run)
        work = opts.workdir / opts.repo
        if work.exists():
            shutil.rmtree(work)
        work.parent.mkdir(parents=True, exist_ok=True)
        remote = f"https://x-access-token:{token}@github.com/{ORG}/{opts.repo}.git" if token else f"https://github.com/{ORG}/{opts.repo}.git"
        if exists:
            run("git", "clone", "-q", "--depth", "1", remote, str(work))
        else:
            work.mkdir()
            run("git", "init", "-q", "-b", "main", cwd=work)
            run("git", "remote", "add", "origin", remote, cwd=work)
    # The runner's (or a Mac's) global ignore rules must not drop files such as folders named "icons".
    run("git", "config", "core.excludesFile", "/dev/null", cwd=work)

    with concurrent.futures.ThreadPoolExecutor(6) as pool:
        lists = dict(zip(assets, pool.map(lambda a: src.files(a, cat[a]), assets)))

    # What the repository already holds: an asset whose source hash is unchanged and whose files are all still there
    # (with their sizes) is carried over as it is, without downloading or hashing it again.
    old_assets, old_rows, old_template = {}, {}, 0
    if (work / ".mirror.json").exists() and (work / "MANIFEST.tsv").exists():
        old_state = json.loads((work / ".mirror.json").read_text())
        old_assets, old_template = old_state["assets"], old_state.get("template", 0)
        for line in (work / "MANIFEST.tsv").read_text().splitlines()[1:]:
            path, size, sha, md5, source = line.split("\t")
            old_rows[path] = {"size": int(size), "sha256": sha, "md5": md5, "url": source}

    def carried(aid):
        rows = {p: r for p, r in old_rows.items() if p.startswith(f"{aid}/")}
        if old_assets.get(aid) != cat[aid]["hash"] or not rows:
            return None
        if all((work / p).is_file() and (work / p).stat().st_size == r["size"] for p, r in rows.items()):
            return rows
        return None

    final = {}  # path -> size, sha256, md5, url: what MANIFEST.tsv lists
    todo = []
    for aid in assets:
        rows = carried(aid)
        if rows:
            final.update(rows)
        else:
            todo.append(aid)
    wanted = {f["path"]: f for a in todo for f in lists[a]}

    def fetch(f):
        dest = work / f["path"]
        if dest.exists() and dest.stat().st_size == f["size"] and (not f["md5"] or md5_of(dest) == f["md5"]):
            return 0
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = get(f["url"], timeout=600)
        if len(data) != f["size"] or (f["md5"] and hashlib.md5(data).hexdigest() != f["md5"]):
            raise SystemExit(f"{f['url']}: size or MD5 does not match the source's listing")
        dest.write_bytes(data)
        return len(data)

    def preview(aid):
        dest = work / "previews" / f"{aid}.webp"
        if dest.exists() and old_assets.get(aid) == cat[aid]["hash"]:
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(get(cat[aid]["thumb"]))

    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        fetched = sum(pool.map(fetch, wanted.values()))
        list(pool.map(preview, assets))

    # glTF models with a .bin over git's limit: split the .bin into parts and point the .gltf at them.
    for path in sorted(p for p in wanted if p.endswith(".gltf")):
        gltf = work / path
        if not gltf_split.needs_split(gltf, FILE_LIMIT):
            continue
        removed, added = gltf_split.split(gltf, FILE_LIMIT)
        folder = posixpath.dirname(path)
        for rel in removed:
            orig = wanted.pop(posixpath.normpath(f"{folder}/{rel}"))
            for part in added:
                wanted[posixpath.normpath(f"{folder}/{part}")] = {
                    "size": (work / folder / part).stat().st_size, "md5": "", "url": orig["url"] + " (split into parts)"}
        wanted[path] = dict(wanted[path], size=gltf.stat().st_size, md5="", url=wanted[path]["url"] + " (buffers split)")
        print(f"{path}: split {', '.join(removed)} into {len(added)} parts")
    for path, f in wanted.items():
        final[path] = {"size": f["size"], "sha256": sha256_of(work / path), "md5": f["md5"], "url": f["url"]}

    keep = set(final) | GENERATED | {f"previews/{a}.webp" for a in assets}
    tracked = run("git", "ls-files", "-z", cwd=work).stdout.split("\0")
    on_disk = {p.relative_to(work).as_posix() for p in work.rglob("*") if p.is_file() and ".git" not in p.relative_to(work).parts[:1]}
    for rel in (set(filter(None, tracked)) | on_disk) - keep:
        (work / rel).unlink(missing_ok=True)

    total = sum(f["size"] for f in final.values())
    rows = ["path\tbytes\tsha256\tmd5\tsource"]
    for path in sorted(final):
        f = final[path]
        rows.append(f"{path}\t{f['size']}\t{f['sha256']}\t{f['md5']}\t{f['url']}")
    (work / "MANIFEST.tsv").write_text("\n".join(rows) + "\n")
    (work / "README.md").write_text(readme(src, opts.repo, info, assets, cat, total))
    (work / ".mirror.json").write_text(json.dumps({
        "source": src.NAME, "site": src.SITE, "license": src.LICENSE_URL,
        "assets": {a: cat[a]["hash"] for a in assets},
        "template": TEMPLATE_VERSION if not opts.self_dir else old_template,
    }, indent=1, sort_keys=True) + "\n")
    for t in ("LICENSE", ".github/verify.py", ".github/workflows/check.yml", ".github/workflows/update.yml"):
        if opts.self_dir and t.startswith(".github/workflows/"):
            continue
        (work / t).parent.mkdir(parents=True, exist_ok=True)
        if t.endswith("update.yml"):
            (work / t).write_text(render_update(src, opts.repo))
        else:
            shutil.copyfile(TEMPLATE / t, work / t)

    run("python3", ".github/verify.py", cwd=work)
    run("git", "add", "-A", cwd=work)
    if not run("git", "status", "--porcelain", cwd=work).stdout.strip():
        print(f"{opts.repo}: up to date ({len(assets)} assets)")
        return
    when = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    msg = f"Mirror {src.TITLE}: {len(assets)} assets, {size_label(total)} ({when})"
    run("git", "-c", f"user.name={BOT[0]}", "-c", f"user.email={BOT[1]}", "commit", "-q", "-m", msg, cwd=work)
    if opts.dry_run:
        print(f"{opts.repo}: dry run, would push: {msg}; downloaded {size_label(fetched)} into {work}")
        return
    push = run("git", "push", "-q", "origin", "HEAD:main", cwd=work, check=False)
    if push.returncode:
        sys.exit(f"pushing {opts.repo} failed: {push.stderr.replace(token, '***') if token else push.stderr}")
    print(f"{opts.repo}: pushed: {msg}; downloaded {size_label(fetched)}")


if __name__ == "__main__":
    main()
