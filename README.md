# cc0-mirror

Keeps copies of other people's **CC0** game assets in the [zuujis-cc0](https://github.com/zuujis-cc0) organisation,
one repository per category of about 1 GB at most, so game makers can browse them and take single files from GitHub.
Each repository credits its source, links the original, and lists every file with its size and SHA-256 in
`MANIFEST.tsv`.

| Source | What is mirrored | Repositories |
| --- | --- | --- |
| [Poly Haven](https://polyhaven.com) | Textures (1K and 2K JPG maps), models (1K and 2K glTF), HDRI skies (1K and 2K .hdr): 2,392 assets, 36.7 GB | 64, `polyhaven-<kind>-<category>[-N]` |
| [ambientCG](https://ambientcg.com) | Materials, decals, atlases and terrains (1K and 2K JPG), HDRIs (1K, 2K), low-poly models, Substance files: 2,933 assets, 73 GB | 108, `ambientcg-<kind>[-<category>][-N]` |
| [OpenGameArt](https://opengameart.org) | Every entry licensed CC0, as uploaded (ZIPs unpacked); Kenney's and Quaternius's uploads first, in their own repositories. About 17,600 entries, ~190 GB, read gradually (robots.txt asks for 10 s between requests) | `opengameart-<uploader>-<type>`, `opengameart-<type>[-N]` |

Not mirrored, linked from [the collection](https://github.com/zuujis-cc0/free-game-assets/blob/main/MORE-CC0.md) instead: itch.io (its robots.txt disallows
downloads), KayKit (it has [official repositories](https://github.com/KayKit-Game-Assets)), Freesound and full-resolution sets (too large).

The originals, in more formats and higher resolutions, stay on each source's own site; please support the people
who make them.

## How it works

Every mirror repository **fills and updates itself**. cc0-mirror only decides what goes where and creates the
repositories:

```
cc0-mirror (this repository)                          each mirror repository
  mirror.yml, daily or by hand                          update.yml, weekly at its own time or when started
   ├ plan: read the catalogues, give new assets a       └ sync --self: download new or changed files,
   │   repository (plan/<source>.json)                     unpack, check them, write README/MANIFEST,
   ├ seed: create new repositories and push their          push with the repository's own token
   │   workflows (nothing else)
   └ dispatch: start each repository whose files differ
       from the plan (a few per source at a time), keep
       every schedule switched on
```

- **`mirror/plan.py`**: an asset keeps its repository for good, so links to it never move. New assets go into the
  last repository of their category while it stays under 900 MB; then a new one (`-2`, `-3`, ...) is started.
- **`mirror/sync.py`**: `--seed` creates a repository and pushes what it needs to fill itself. `--self DIR` (what
  `update.yml` runs) downloads every file (checked against the source's size, and MD5 where given; ZIPs, 7z and RAR
  unpacked; unchanged assets carried over without downloading), writes `previews/`, `README.md`, `MANIFEST.tsv` and
  `.mirror.json`, runs the repository's check and pushes. `--dry-run` fills a fresh copy in `work/`, touching nothing.
- **`mirror/dispatch.py`**: starts repositories' updates, at most `MAX_PARALLEL` per source at once, waits for each.
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
python3 -m mirror.plan polyhaven,ambientcg          # update the plans, list what to set up and start
python3 -m mirror.sync polyhaven polyhaven-textures-wood --dry-run
MIRROR_TOKEN=... python3 -m mirror.sync polyhaven polyhaven-textures-wood --seed
python3 -m unittest discover -s tests -t .
```

## Adding a source

Write `mirror/sources/<name>.py` with the same functions as `polyhaven.py`, add it to the default `sources` in
`.github/workflows/mirror.yml`, and only take assets whose licence is CC0 on the source's own page. Open an issue
first if you would like to suggest one.

Problems: [open an issue](https://github.com/zuujis-cc0/cc0-mirror/issues/new), or tell us privately with the
[report form](https://games.zuujis.com/report). The code here is CC0 too, like everything in zuujis-cc0.
