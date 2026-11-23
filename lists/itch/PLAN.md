# itch.io: CC0 catalogue, links only (planned)

**The owner's decision (2026-11-23):** itch.io is *not* mirrored. Its robots.txt (https://itch.io/robots.txt) disallows
`/game/download/`, `/bundle/download/`, `/search`, `/embed/`, `/checkout/` and a few more. The owner agreed to **an
`itch-cc0` catalogue repository: title, creator, tags and a link for each CC0 asset pack, built from the pages robots.txt
allows, sending people to itch.io for the downloads.** No files from the packs are copied anywhere.

## What itch.io offers (checked 2026-11-23)

- **Listing filtered by asset licence:** `https://itch.io/game-assets/assets-cc0` ("2,949 results" that day);
  `https://itch.io/game-assets/assets-cc0/free` for the free ones. Sub-filters exist, e.g. `/genre-rpg`,
  `/genre-platformer`. Use the licence filter for discovery and confirm each licence on the pack page.
- **Paging as JSON:** add `?page=N&format=json` → `{"content": "<html of the cells>", "num_items": 36, "page": N}`;
  stop when `num_items` is 0. Each cell: `<div data-game_id="ID" class="game_cell ...">`, title link
  `<a class="title game_link" href="https://CREATOR.itch.io/SLUG">TITLE</a>`, short text in `class="game_text"`,
  creator in `class="game_author"` (`<a href="https://CREATOR.itch.io">NAME</a>`), cover `data-lazy_src` on
  `img.itch.zone`.
- **The pack's own page** (`https://CREATOR.itch.io/SLUG`, allowed) has the "More information" table as
  `<tr><td>NAME</td><td>VALUE</td></tr>` rows: `Status`, `Category` (Assets), `Author`, `Tags` (comma-separated),
  **`Asset license` (e.g. "Creative Commons Zero v1.0 Universal": only list a pack whose row says exactly CC0)**,
  `Content` (e.g. "No generative AI was used"), `Rating`, `Made with`. Price is on the page too (free / name your own
  price / paid).
- itch.io's terms of service (https://itch.io/docs/legal/terms) had no clause about crawling or automated access when
  read on 2026-11-23; robots.txt has no Crawl-delay. Still go gently: one request every 3 s, a User-Agent naming the
  repository, and never a download URL.

## Build

- Code in cc0-mirror `lists/itch/` (standard library only, like `mirror/`); a public repository
  `zuujis-cc0/itch-cc0` that **updates itself** weekly with its own workflow, the same pattern as the mirrors:
  `update.yml` checks out itself and cc0-mirror and runs the script with its own GITHUB_TOKEN; created and given its
  workflows once with MIRROR_TOKEN (a seed step, or by hand with `gh repo create` + a push from an admin).
- Each run: walk the `assets-cc0` listing (about 82 pages of 36), then fetch the page of every pack not seen before,
  and re-check known ones in rotation (e.g. a seventh of them a week) so removed packs and changed licences drop out.
  Keep state in `catalogue.json` in the repository (id, title, url, creator, creator_url, tags, license, price,
  short text, first_seen, checked). A full first pass is about 3,000 pages at 3 s ≈ 2.5 h; split it over runs with a
  time budget like OpenGameArt's (`OGA_BUDGET`) if needed.
- `README.md` generated from it: intro (not affiliated with itch.io; downloads happen on itch.io; support the
  creators), then sections by kind from the tags (2D/pixel art, 3D, audio/music/sound effects, UI/icons, fonts,
  textures/tiles, VFX, other), each a table: title → itch page, creator → their itch page, tags, free or price.
  Covers: either none, or hotlinked `img.itch.zone` thumbnails (never copied into the repository). Big sections
  may need one Markdown file each (`lists/<kind>.md`) to stay readable; GitHub renders up to about 512 KB per file.
- LICENSE: CC0 for the list. A `check` workflow validating `catalogue.json` and that the README matches it.
- Then link it from the hub's `MORE-CC0.md` (PR), the org profile, cc0-mirror's README ("Not mirrored"), and
  games.zuujis.com `/assets` (needs a release; deploy only with the owner's yes).

## State

- 2026-11-23: plan only; nothing built. Pages above checked by hand (a few requests).
