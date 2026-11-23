"""Decide which repository holds each asset, and which repositories are out of date.

    python3 -m mirror.plan SOURCE [--matrix] [--limit N] [--only REPO,REPO]

Reads the source's catalogue, updates plan/SOURCE.json and prints the repositories that need a sync (as a JSON list
with --matrix, for a GitHub Actions matrix; one per line without).

An asset keeps its repository for good, so links to it never move: a new asset goes into the last repository of its
group while that stays under common.REPO_LIMIT, otherwise into a new one (name, name-2, name-3, ...). A repository is
out of date when its .mirror.json (written by sync.py) does not list exactly the plan's assets with the same hashes,
or when it does not exist yet.
"""
import argparse
import concurrent.futures
import json
import sys
import urllib.error

from mirror.sync import TEMPLATE_VERSION
from mirror.common import FILE_LIMIT, ORG, REPO_LIMIT, get, load_plan, load_source, save_plan


def asset_bytes(files):
    # git stores identical content once, so a file shared between resolutions counts once.
    return sum({f["md5"] or f["path"]: f["size"] for f in files}.values())


def assign(plan, src, aid, meta, size):
    base = src.repo_base(meta["group"])
    names = sorted((r for r, info in plan["repos"].items() if info["group"] == meta["group"]),
                   key=lambda r: int(r.rsplit("-", 1)[1]) if r != base and r.rsplit("-", 1)[1].isdigit() else 1)
    if names and plan["repos"][names[-1]]["bytes"] + size <= REPO_LIMIT:
        repo = names[-1]
    else:
        repo = base if not names else f"{base}-{len(names) + 1}"
        plan["repos"][repo] = {"group": meta["group"], "category": meta["category"], "kind": meta["kind"], "bytes": 0}
    plan["repos"][repo]["bytes"] += size
    return repo


def mirrored_state(repo):
    """The repository's .mirror.json, or None when there is none yet (the repository is new or empty)."""
    try:
        return json.loads(get(f"https://raw.githubusercontent.com/{ORG}/{repo}/main/.mirror.json", tries=3))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--matrix", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="at most this many repositories (0: all)")
    ap.add_argument("--only", default="", help="comma-separated repositories to consider")
    opts = ap.parse_args()
    src = load_source(opts.source)
    plan = load_plan(opts.source)
    cat = src.catalogue()

    gone = [a for a in plan["assets"] if a not in cat]
    for aid in gone:
        old = plan["assets"].pop(aid)
        plan["repos"][old["repo"]]["bytes"] -= old["bytes"]
    for aid in [a for a in plan["skipped"] if a not in cat]:
        del plan["skipped"][aid]
    known = lambda aid: plan["assets"].get(aid, {}).get("hash") or plan["skipped"].get(aid, {}).get("hash")
    todo = [aid for aid, m in sorted(cat.items()) if known(aid) != m["hash"]]
    with concurrent.futures.ThreadPoolExecutor(6) as pool:
        lists = dict(zip(todo, pool.map(lambda a: src.files(a, cat[a]), todo)))
    sizes = {aid: asset_bytes(fs) for aid, fs in lists.items()}
    for aid in todo:
        # A glTF model's .bin can be split into parts by sync.py; any other file must fit as it is.
        largest = max((f["size"] for f in lists[aid] if not (cat[aid]["kind"] == "models" and f["path"].endswith(".bin"))), default=0)
        plan["skipped"].pop(aid, None)
        if largest > FILE_LIMIT:
            old = plan["assets"].pop(aid, None)
            if old:
                plan["repos"][old["repo"]]["bytes"] -= old["bytes"]
            plan["skipped"][aid] = {"hash": cat[aid]["hash"], "why": f"a file of {largest / 1e6:.0f} MB, over GitHub's limit"}
            continue  # sorted, so a first run fills repositories in a stable order
        old = plan["assets"].get(aid)
        if old:
            plan["repos"][old["repo"]]["bytes"] += sizes[aid] - old["bytes"]
            old.update(hash=cat[aid]["hash"], bytes=sizes[aid])
        else:
            plan["assets"][aid] = {"repo": assign(plan, src, aid, cat[aid], sizes[aid]), "hash": cat[aid]["hash"], "bytes": sizes[aid]}
    save_plan(opts.source, plan)

    repos = sorted(plan["repos"])
    if opts.only:
        repos = [r for r in repos if r in opts.only.split(",")]
    want = {r: {} for r in repos}
    for aid, a in plan["assets"].items():
        if a["repo"] in want:
            want[a["repo"]][aid] = a["hash"]
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        have = dict(zip(repos, pool.map(mirrored_state, repos)))
    # central: new repositories, and ones whose workflow files are older than the template (only cc0-mirror's token
    # may write workflows). dispatch: the rest that are out of date; their own update workflow is started.
    central = [r for r in repos if have[r] is None or have[r].get("template", 0) != TEMPLATE_VERSION]
    dispatch = [r for r in repos if r not in central and have[r]["assets"] != want[r]]
    if opts.limit:
        central, dispatch = central[: opts.limit], dispatch[: opts.limit]
    print(f"{opts.source}: {len(plan['assets'])} assets in {len(plan['repos'])} repositories; "
          f"{len(todo)} new or changed, {len(gone)} gone, {len(plan['skipped'])} skipped; "
          f"{len(central)} to fill here, {len(dispatch)} to update themselves", file=sys.stderr)
    if opts.matrix:
        print(f"central={json.dumps(central)}")
        print(f"central_count={len(central)}")
        print(f"dispatch={json.dumps(dispatch)}")
        print(f"repos={json.dumps(sorted(plan['repos']))}")
    else:
        print("\n".join(f"{r}\tcentral" for r in central) + ("\n" if central else "") + "\n".join(f"{r}\tdispatch" for r in dispatch))


if __name__ == "__main__":
    main()
