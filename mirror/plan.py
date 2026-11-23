"""Decide which repository holds each asset, and which repositories are out of date.

    python3 -m mirror.plan SOURCE[,SOURCE...] [--matrix] [--published-plan] [--limit N] [--only REPO,REPO]

Reads each source's catalogue, updates plan/SOURCE.json and lists the repositories to set up (seed: create them and
give them their workflows) and the ones to start (dispatch: they fill or update themselves), as GitHub Actions outputs
with --matrix.

An asset keeps its repository for good, so links to it never move: a new asset goes into the last repository of its
group while that stays under common.REPO_LIMIT, otherwise into a new one (name, name-2, name-3, ...). A repository is
out of date when its .mirror.json (written by sync.py) does not list exactly the plan's assets with the same hashes,
or when it does not exist yet.
"""
import argparse
import concurrent.futures
import inspect
import json
import sys
import urllib.error

from mirror.sync import TEMPLATE_VERSION
from mirror.common import need_org, FILE_LIMIT, ORG, REPO_LIMIT, get, load_plan, load_source, save_plan


def asset_bytes(files):
    # git stores identical content once, so a file shared between resolutions counts once. A ZIP is counted at its
    # own size (its contents are mostly images, which do not compress further).
    return sum({f.get("md5") or f["path"]: f["size"] for f in files}.values())


def too_big(files):
    """The largest file that would have to go into git as it is (ZIPs are unpacked; glTF buffers can be split)."""
    return max((f["size"] for f in files if not f.get("unzip") and not f.get("splittable")), default=0)


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


def refresh_plan(name, plan):
    """Refresh the saved plan from the source catalogue."""
    src = load_source(name)
    takes_previous = "previous" in inspect.signature(src.catalogue).parameters
    cat = src.catalogue(plan) if takes_previous else src.catalogue()

    gone = [a for a in plan["assets"] if a not in cat]
    for aid in gone:
        old = plan["assets"].pop(aid)
        plan["repos"][old["repo"]]["bytes"] -= old["bytes"]
    for aid in [a for a in plan["skipped"] if a not in cat]:
        del plan["skipped"][aid]
    known = lambda aid: plan["assets"].get(aid, {}).get("hash") or plan["skipped"].get(aid, {}).get("hash")
    todo = [aid for aid, m in sorted(cat.items()) if known(aid) != m["hash"]]
    with concurrent.futures.ThreadPoolExecutor(getattr(src, "THREADS", 6)) as pool:
        lists = dict(zip(todo, pool.map(lambda a: src.files(a, cat[a]), todo)))
    sizes = {aid: asset_bytes(fs) for aid, fs in lists.items()}
    for aid in todo:  # sorted, so a first run fills repositories in a stable order
        largest = too_big(lists[aid])
        plan["skipped"].pop(aid, None)
        if largest > FILE_LIMIT and all(not f.get("unzip") and not f.get("splittable") and f["size"] > FILE_LIMIT for f in lists[aid]):
            # Nothing of it would fit in git.
            old = plan["assets"].pop(aid, None)
            if old:
                plan["repos"][old["repo"]]["bytes"] -= old["bytes"]
            plan["skipped"][aid] = {"hash": cat[aid]["hash"], "why": f"a file of {largest / 1e6:.0f} MB, over GitHub's limit"}
            continue
        old = plan["assets"].get(aid)
        if old:
            plan["repos"][old["repo"]]["bytes"] += sizes[aid] - old["bytes"]
            old.update(hash=cat[aid]["hash"], bytes=sizes[aid])
        else:
            plan["assets"][aid] = {"repo": assign(plan, src, aid, cat[aid], sizes[aid]), "hash": cat[aid]["hash"], "bytes": sizes[aid]}
    # What a repository needs to know about its assets (names, authors, pictures, links, files), so its own update
    # does not have to read the whole catalogue.
    for aid, a in plan["assets"].items():
        a["meta"] = {k: v for k, v in cat[aid].items() if k != "hash"}
    save_plan(name, plan)
    print(f"{name}: {len(todo)} new or changed, {len(gone)} gone, {len(plan['skipped'])} skipped",
          file=sys.stderr)


def plan_source(name, only=(), limit=0, published_plan=False):
    """Return (seed, dispatch, repos), refreshing the plan unless told to use it as published."""
    plan = load_plan(name)
    if not published_plan:
        refresh_plan(name, plan)
    repos = sorted(plan["repos"])
    if only:
        repos = [r for r in repos if r in only]
    want = {r: {} for r in repos}
    for aid, a in plan["assets"].items():
        if a["repo"] in want:
            want[a["repo"]][aid] = a["hash"]
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        have = dict(zip(repos, pool.map(mirrored_state, repos)))
    # seed: repositories that do not exist yet or have no workflows, or older workflows than the template (only
    # cc0-mirror's token may create repositories and write workflows). dispatch: every repository whose files are not
    # what the plan says, seeded ones included: each fills or updates itself in its own update workflow.
    seed = [r for r in repos if have[r] is None or have[r].get("template", 0) != TEMPLATE_VERSION]
    dispatch = [r for r in repos if have[r] is None or have[r].get("assets") != want[r]]
    if limit:
        dispatch = dispatch[:limit]
        seed = [r for r in seed if r in dispatch or have[r] is not None][:limit]
    print(f"{name}: {len(plan['assets'])} assets in {len(plan['repos'])} repositories; "
          f"{len(seed)} to set up, {len(dispatch)} to fill or update themselves", file=sys.stderr)
    return seed, dispatch, repos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sources", help="comma-separated, e.g. polyhaven,ambientcg")
    ap.add_argument("--matrix", action="store_true", help="print GitHub Actions outputs")
    ap.add_argument("--limit", type=int, default=0, help="at most this many repositories per source (0: all)")
    ap.add_argument("--published-plan", action="store_true", help="use saved plans without refreshing source catalogues")
    ap.add_argument("--only", default="", help="comma-separated repositories to consider")
    opts = ap.parse_args()
    need_org()
    only = set(filter(None, opts.only.split(",")))
    seed, dispatch, repos = [], [], []
    for name in filter(None, opts.sources.split(",")):
        s, d, r = plan_source(name, only, opts.limit, published_plan=opts.published_plan)
        seed += [{"source": name, "repo": x} for x in s]
        dispatch += [{"source": name, "repo": x} for x in d]
        repos += [{"source": name, "repo": x} for x in r]
    seed = seed[:250]  # a matrix holds at most 256 jobs; the rest are set up next run
    if opts.matrix:
        print(f"seed={json.dumps(seed)}")
        print(f"seed_count={len(seed)}")
        print(f"dispatch={json.dumps(dispatch)}")
        print(f"repos={json.dumps(repos)}")
    else:
        for item in seed:
            print(f"{item['source']}\t{item['repo']}\tseed")
        for item in dispatch:
            print(f"{item['source']}\t{item['repo']}\tdispatch")

if __name__ == "__main__":
    main()
