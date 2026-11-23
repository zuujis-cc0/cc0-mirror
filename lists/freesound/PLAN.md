# Freesound: hand-picked CC0 list (work in progress)

**The owner's decision (2026-11-23):** Freesound is *not* mirrored. Its API terms
(https://freesound.org/help/tos_api/) forbid using the API to replicate Freesound on another site and using robots or
scraping to build similar databases, commercial or not; original downloads need OAuth2 and are rate-limited. The
owner agreed to **hand-picked lists of CC0 sounds and packs that link to Freesound and credit each creator**. No
sound files are copied anywhere.

## Goal

A links-only repository `zuujis-cc0/freesound-cc0`:

- `packs.json`: the chosen packs (creator, pack id, name, number of sounds, licence, category, one-line note).
- `README.md`, generated from it: one table per game category (UI and menus, footsteps, impacts and hits, weapons and
  explosions, magic and sci-fi, creatures and voices, 8-bit and retro, ambience and nature, vehicles and machines,
  music loops and stingers), each row: pack name → `https://freesound.org/people/<user>/packs/<id>/`, creator →
  `https://freesound.org/people/<user>/`, sounds, "CC0". Header: what this is, that downloads happen on Freesound
  (free account needed), please credit/thank the creators and consider donating to Freesound
  (https://freesound.org/donations/donate/), not affiliated with Freesound.
- `LICENSE`: CC0 for the list itself. A `check` workflow (like the mirror repos' `check.yml`: pinned
  `actions/checkout`, read-only token) validating `packs.json` and that README matches it.
- Optional later, only with an API key the owner registers (secret `FREESOUND_TOKEN`): a weekly workflow that asks
  the official API whether each pack is still online and still CC0, and opens an issue if not. Do not scrape for this.
- Then link it from the hub's `MORE-CC0.md` (PR, the hub ruleset needs one), the org profile `zuujis-cc0/.github`
  `profile/README.md`, cc0-mirror's README ("Not mirrored" paragraph), and games.zuujis.com `/assets` (games-rs
  `src/app/assets.rs`; a release and deploy need the owner's yes).

## How the candidates are found (`packs.py`)

robots.txt (https://freesound.org/robots.txt) disallows `/search/`, `/browse/`, `/apiv2/` and more, but not
`/people/<user>/packs/`. `packs.py` reads one creator's pack listing at a time, 5 s apart, like a person browsing, and
records each pack's licence as the listing shows it (`title="License: Creative Commons 0"`; mixed packs show another
value and are left out). It is for curating a few dozen creators by hand, not for crawling.

    python3 packs.py Breviceps,unfa,qubodup 1        # creators, pages per creator (15 packs a page); writes packs.json
    python3 packs.py more,users 3 append             # a third argument keeps what packs.json already has

Creators were picked as known CC0 game-sound makers; the script confirms the licence, so a wrong guess just shows
0 CC0 packs. Choose packs useful for games (not every pack), prefer well-downloaded ones, and check a pack page by
hand if its name is unclear. Never download sound files.

## State

- 2026-11-23: first pass over 25 creators (first listing page each, 15 packs a page) finished; results in `packs.json`. Creators with more pages (unfa has 75 packs) need `python3 packs.py NAME 5 append`. Not found or no packs: felixblume, Leszek_Szary, suntemple, Fins, nsstudios, MATRIXXX_ (check their exact usernames).
  First results: Breviceps 5/5 CC0, unfa 15/15 (75 packs in all, more pages), qubodup 14/15.
- Next: finish the pass, pick packs per category, build the repository as above, then the links.
