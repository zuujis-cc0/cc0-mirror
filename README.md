# cc0-mirror

Keeps copies of other people's **CC0** game assets in the [zuujis-cc0](https://github.com/zuujis-cc0) organisation,
one repository per category of about 1 GB at most, so game makers can browse them and take single files from GitHub.
Each repository credits its source, links the original, and lists every file with its size and SHA-256 in
`MANIFEST.tsv`.

| Source | What is mirrored | Repositories |
| --- | --- | --- |
| [Poly Haven](https://polyhaven.com) | Textures (1K and 2K JPG maps), models (1K and 2K glTF), HDRI skies (1K and 2K .hdr): 2,392 assets, 36.7 GB | 64 named `polyhaven-<kind>-<category>[-N]` |

The originals, in more formats and higher resolutions, stay on each source's own site; please support the people
who make them.

## How it works

```
cc0-mirror (this repository)                          each mirror repository
  mirror.yml, weekly or by hand                         update.yml, weekly at its own time
   ├ plan: read the catalogue, give new assets a        └ sync --self: download new or changed files,
   │   repository (plan/<source>.json)                     check them, write README/MANIFEST, push
   ├ fill: create new repositories and fill them          with the repository's own token
   └ dispatch: start updates where the plan changed,
       keep every schedule switched on
```

- **`mirror/plan.py`**: an asset keeps its repository for good, so links to it never move. New assets go into the
  last repository of their category while it stays under 900 MB; then a new one (`-2`, `-3`, ...) is started.
- **`mirror/sync.py`**: creates the repository if needed, downloads every file (each checked against the source's size
  and MD5; unchanged assets are carried over without downloading), writes `previews/`, `README.md`, `MANIFEST.tsv`,
  `.mirror.json`, `LICENSE` and the workflows, runs the repository's own check and pushes. `--dry-run` does it all
  in `work/` without touching GitHub. `--self DIR` updates a checkout in place (what `update.yml` runs).
- **`mirror/gltf_split.py`**: git refuses files over 100 MB. A glTF model whose `.bin` is bigger (some of Poly Haven's
  trees reach 949 MB) has its buffer split into parts of at most 90 MB, and its `.gltf` lists the parts. The model
  loads exactly as before.
- **`mirror/sources/<name>.py`**: one module per source (`catalogue()`, `files()`, `repo_base()`). Sources that ask for
  it get a User-Agent naming this project and gentle request rates.
- Every mirror repository runs `check` (`.github/verify.py`) on every push: each file matches `MANIFEST.tsv`, and
  nothing unlisted got in.

## Setup

The `mirror` workflow needs an organisation secret **`MIRROR_TOKEN`**: a fine-grained personal access token with
resource owner **zuujis-cc0**, access to **all repositories**, and read/write for **Administration** (create
repositories), **Contents**, **Workflows** and **Actions** (start and re-enable `update.yml`). The mirror
repositories' own updates need no secret.

Run by hand: *Actions → mirror → Run workflow* (a source, optionally some repositories, and a limit for a trial run).
Locally (Python 3.10+, standard library only):

```bash
python3 -m mirror.plan polyhaven                    # update plan/polyhaven.json, list what is out of date
python3 -m mirror.sync polyhaven polyhaven-textures-wood --dry-run
MIRROR_TOKEN=... python3 -m mirror.sync polyhaven polyhaven-textures-wood
python3 -m unittest discover -s tests -t .
```

## Adding a source

Write `mirror/sources/<name>.py` with the same functions as `polyhaven.py`, add it to the `source` choices in
`.github/workflows/mirror.yml`, and only take assets whose licence is CC0 on the source's own page. Open an issue
first if you would like to suggest one.

Problems: [open an issue](https://github.com/zuujis-cc0/cc0-mirror/issues/new), or tell us privately with the
[report form](https://games.zuujis.com/report). The code here is CC0 too, like everything in zuujis-cc0.
