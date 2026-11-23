"""Start mirror repositories' own update workflows, a few at a time, and keep every repository's schedule on.

    MIRROR_TOKEN=... python3 -m mirror.dispatch DISPATCH_JSON REPOS_JSON [--budget SECONDS]

DISPATCH_JSON and REPOS_JSON are plan.py's outputs (lists of {"source", "repo"}). Each source says how many of its
repositories may fill at once (MAX_PARALLEL: OpenGameArt's robots.txt asks for one request every 10 seconds, so one
at a time there). A repository is started, then watched until its run finishes, then the next one of that source is
started; whatever the budget does not reach is started by the next run of cc0-mirror. Every repository's update
workflow is also switched on again, because GitHub switches schedules off after 60 days without activity.
"""
import argparse
import datetime
import json
import sys
import time

from mirror.common import ORG, gh, load_source

POLL = 30


def now():
    return datetime.datetime.now(datetime.timezone.utc)


def start(repo):
    for attempt in range(6):  # a repository seeded seconds ago may not have its workflow registered yet
        status, body = gh("POST", f"repos/{ORG}/{repo}/actions/workflows/update.yml/dispatches", {"ref": "main"})
        if status == 204:
            return True
        time.sleep(10 * (attempt + 1))
    print(f"{repo}: could not start its update ({status} {body})", file=sys.stderr)
    return False


def run_of(repo, since):
    """The update run started at or after since: (status, conclusion, url) or None."""
    status, body = gh("GET", f"repos/{ORG}/{repo}/actions/workflows/update.yml/runs?event=workflow_dispatch&per_page=5")
    if status != 200:
        return None
    for r in body.get("workflow_runs", []):
        created = datetime.datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
        if created >= since - datetime.timedelta(seconds=5):
            return r["status"], r.get("conclusion"), r["html_url"]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dispatch")
    ap.add_argument("repos")
    ap.add_argument("--budget", type=int, default=int(5.5 * 3600))
    opts = ap.parse_args()
    todo, every = json.loads(opts.dispatch), json.loads(opts.repos)
    deadline = time.monotonic() + opts.budget

    for item in every:
        gh("PUT", f"repos/{ORG}/{item['repo']}/actions/workflows/update.yml/enable")

    limits = {}
    queues = {}
    for item in todo:
        queues.setdefault(item["source"], []).append(item["repo"])
        limits.setdefault(item["source"], getattr(load_source(item["source"]), "MAX_PARALLEL", 4))
    running = {}  # repo -> (source, started at)
    done, failed = [], []
    while (any(queues.values()) or running) and time.monotonic() < deadline:
        for source, queue in queues.items():
            while queue and sum(1 for s, _ in running.values() if s == source) < limits[source]:
                repo = queue.pop(0)
                t = now()
                if start(repo):
                    running[repo] = (source, t)
                    print(f"started {repo}")
                else:
                    failed.append(repo)
        time.sleep(POLL)
        for repo, (source, t) in list(running.items()):
            r = run_of(repo, t)
            if r and r[0] == "completed":
                del running[repo]
                (done if r[1] == "success" else failed).append(repo)
                print(f"{repo}: {r[1]} {r[2]}")
            elif not r and (now() - t).total_seconds() > 900:
                del running[repo]  # never showed up; the next cc0-mirror run will start it again
                failed.append(repo)
                print(f"{repo}: no run appeared")
    left = sum(len(q) for q in queues.values())
    print(f"{len(done)} filled or updated, {len(failed)} failed, {len(running)} still running, {left} left for the next run")
    if failed:
        print("failed: " + ", ".join(failed))


if __name__ == "__main__":
    main()
