# Notes for coding agents (Codex, Claude Code)

Read README.md first. The rules the owner set:

- **Every mirror repository fills and updates itself.** cc0-mirror only creates a repository and gives it what it
  needs (`sync.py --seed`: workflows, LICENSE, a placeholder README); the files arrive through the repository's own
  `update.yml` (`sync.py --self`). Do not add a path where cc0-mirror pushes asset files into a repository.
- **Respect each source.** Poly Haven: a User-Agent naming the client (`common.UA`). OpenGameArt: robots.txt asks for
  10 seconds between requests; `common.SPACING` enforces it and `MAX_PARALLEL = 1` keeps one OpenGameArt repository
  filling at a time. itch.io disallows `/game/download/` in robots.txt, so it is not mirrored. Never get around a
  site's download guard (Kenney's own site answers forbidden to scripts; Kenney's OpenGameArt uploads are fine).
- **Only CC0.** A source module only returns assets whose licence on the source's own page is CC0.
- **Assets never move.** `plan.py` gives an asset a repository for good; links to it must keep working.
- **Keep repositories small:** at most `REPO_LIMIT` (900 MB); files under git's 100 MiB limit (`FILE_LIMIT`; glTF
  buffers are split by `gltf_split.py`, other big files are listed in the README as not copied).
- **Workflows:** a repository's own token cannot change `.github/workflows/`. After changing a workflow template,
  bump `TEMPLATE_VERSION` in `mirror/sync.py` so the next `mirror` run re-seeds every repository.
- Only actions published by GitHub, pinned by full commit SHA. Python standard library only (7z is used when present).
- Test before pushing: `python3 -m unittest discover -s tests -t .`, then
  `python3 -m mirror.sync SOURCE REPO --dry-run` and `--self work/REPO` on a small repository.
- Commits: identity zuujiscom, no Co-Authored-By trailer.
