"""Write the catalogue: every mirrored asset grouped by what it is (wood, stone, skies, furniture...), across sources.

    python3 -m mirror.catalog --out DIR              # writes DIR/README.md, DIR/topics/*.md and DIR/catalog.json
    python3 -m mirror.catalog --out DIR --setup      # also LICENSE and the daily update workflow (a new catalogue)

The mirror repositories are split by source and size, which is how they are kept but not how people look for things.
The catalogue regroups them by topic: a Poly Haven wood texture, an ambientCG wood floor and its Substance file all
appear under Wood. Each topic page shows every asset with its picture (from the repository holding it) and links to its
folder and to the original. Assets a repository has not received yet are listed without a picture until it has.

Reads plan/SOURCE.json (what exists and where it goes) and each repository's .mirror.json (what it holds now). The
catalogue repository (ORG/cc0-catalog) runs this every day in its own workflow
(mirror/template-catalog/.github/workflows/update.yml, written by --setup).
Standard library only.
"""
import argparse
import concurrent.futures
import datetime
import html
import json
import re
import urllib.error
from pathlib import Path

from mirror.common import CODE_REPO, ORG, ROOT, get, load_plan, need_org

SOURCES = (("polyhaven", "Poly Haven"), ("ambientcg", "ambientCG"), ("opengameart", "OpenGameArt"))
PAGE = 240  # assets per topic page (pictures load slowly past that)
ROW = 6

# Sections and their topics, in the order shown.
SECTIONS = [
    ("surfaces", "🧱 Textures and materials", [
        ("wood", "Wood", "Planks, floors, bark, bamboo, painted and raw wood"),
        ("brick", "Brick and block", "Brick walls, blocks and painted brick"),
        ("stone", "Stone and rock", "Rock faces, cobbles, marble, granite and terrazzo"),
        ("paving", "Roads and paving", "Asphalt, roads, paving stones and paths"),
        ("tiles", "Tiles and roofing", "Floor and wall tiles, ceramics and roof tiles"),
        ("walls", "Concrete, plaster and walls", "Concrete, plaster, facades and wallpaper"),
        ("ground", "Ground, grass and snow", "Soil, gravel, grass, moss, sand, snow, ice and lava"),
        ("metal", "Metal", "Plates, sheet metal, rust, grates, fences and painted metal"),
        ("fabric", "Fabric and leather", "Cloth, carpet, leather, wicker and rope"),
        ("wear", "Dirt, scratches and wear", "Fingerprints, smears, scratches, leaks and other imperfections"),
        ("other-surfaces", "Plastic, paper and more", "Plastic, paper, cardboard, foam, food and everything else"),
    ]),
    ("overlays", "🩹 Decals and atlases", [
        ("decals", "Decals", "Road markings, leaks, manhole covers and other things to lay on a surface"),
        ("atlases", "Atlases and brushes", "Sheets of leaves, foliage, tape and small objects with transparency"),
    ]),
    ("skies", "🌅 HDRI skies and scenes", [
        ("sky-day", "Daytime skies", "Clear and cloudy skies by day and in the morning"),
        ("sky-night", "Sunset and night", "Evening, sunset and night skies and scenes"),
        ("scene-nature", "Countryside and nature", "Fields, forests, mountains, parks, coasts and deserts"),
        ("scene-town", "Towns and industry", "Streets, towns, industrial sites and ruins"),
        ("scene-outdoor", "Other outdoor scenes", "Outdoor environments by day"),
        ("scene-indoor", "Interiors and studios", "Rooms, halls and photo studios for lighting objects"),
    ]),
    ("models", "🪑 3D models", [
        ("furniture", "Furniture, decor and lights", "Chairs, tables, shelves, ornaments and lamps"),
        ("nature-models", "Plants, rocks and nature", "Trees, plants, rocks, logs and stumps"),
        ("food", "Food and kitchen", "Fruit, bread, cakes, dishes and kitchen things"),
        ("tools", "Tools and weapons", "Hand tools, equipment and weapons"),
        ("vehicles", "Vehicles", "Cars, boats and other transport"),
        ("buildings", "Buildings and industry", "Architecture, industrial and infrastructure pieces"),
        ("objects", "Everyday objects", "Clothing, containers, electronics, office and leisure things"),
    ]),
    ("game-art", "🎮 Game art", [
        ("art-2d", "2D art and sprites", "Sprites, tiles, interfaces and backgrounds"),
        ("art-3d", "3D game art", "Game-ready models and packs"),
        ("game-textures", "Game textures", "Textures made for games"),
        ("music", "Music", "Songs and loops"),
        ("sounds", "Sound effects", "Effects for games"),
        ("concept", "Concept art", "Sketches and concept pieces"),
        ("game-other", "Other game assets", "Documents, fonts and everything else"),
    ]),
]
TOPICS = {slug: (sec, title, about) for sec, _, items in SECTIONS for slug, title, about in items}

# Poly Haven's own categories.
POLYHAVEN = {
    "hdris": {"Pure Skies": "sky-day", "Fields & Countryside": "scene-nature", "Forest & Woodland": "scene-nature",
              "Mountains & Hills": "scene-nature", "Parks & Gardens": "scene-nature", "Coast & Water": "scene-nature",
              "Desert & Arid": "scene-nature", "Streets & Town": "scene-town", "Industrial": "scene-town",
              "Abandoned & Ruins": "scene-town", "Interiors": "scene-indoor", "Studio": "scene-indoor"},
    "models": {"Furniture": "furniture", "Decor & Art": "furniture", "Lighting": "furniture", "Nature": "nature-models",
               "Food & Kitchen": "food", "Tools & Equipment": "tools", "Weapons": "tools",
               "Vehicles & Transport": "vehicles", "Architecture": "buildings",
               "Industrial & Infrastructure": "buildings"},
}
# Words in a category or an asset's name, first match wins (so "painted metal" is metal, not paint).
WORDS = [
    ("wear", "surface imperfections imperfection fingerprints smear scratches chip footsteps leaking stain dirt"),
    ("metal", "metal steel diamond plate corrugated rust sheetmetal chainmail grate pipe foil solar panel fence rails"),
    ("wood", "wood planks bark chipboard bamboo cork tree end logs tatami"),
    ("brick", "brick bricks block"),
    ("paving", "asphalt bitumen road paving pathway tactile"),
    ("tiles", "tiles tile roofing ceramic terracotta porcelain thatched"),
    ("stone", "stone rock rocks granite marble onyx travertine terrazzo"),
    ("walls", "concrete plaster facade wallpaper office ceiling"),
    ("ground", "ground terrain gravel grass snow ice moss lava leaves leaf clay sand soil organic"),
    ("fabric", "fabric textiles leather carpet wicker rope net"),
]
FOOD = "apple avocado bread cake cookie cucumber kiwi lemon mango melon pastry pear food"
OPENGAMEART = {"2d-art": "art-2d", "3d-art": "art-3d", "texture": "game-textures", "music": "music",
               "sound-effect": "sounds", "concept-art": "concept"}


def words_of(text):
    """'PaintedBricks' or 'Painted Bricks' -> 'painted bricks'."""
    return " ".join(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text).lower().replace("&", " ").split())


def prefix(aid):
    """ambientCG ids name the material: 'WoodFloorSubstance009' -> 'WoodFloor', 'DaySkyHDRI012' -> 'DaySky'."""
    name = re.match(r"(?:3D)?([A-Za-z]+)", aid).group(1)
    return re.sub(r"(Substance|HDRI|Set)$", "", name)


def by_words(text, default="other-surfaces"):
    found = set(words_of(text).split())
    for slug, ws in WORDS:
        if found & set(ws.split()):
            return slug
    return default


def topic_of(source, aid, meta):
    kind, category = meta.get("kind", ""), meta.get("category") or ""
    if source == "polyhaven":
        if kind == "textures":
            return by_words(category)
        return POLYHAVEN.get(kind, {}).get(category, "objects")
    if source == "ambientcg":
        name = prefix(aid)
        if kind in ("hdris", "hdri-elements"):
            if "Indoor" in name:
                return "scene-indoor"
            if re.search(r"Evening|Night", name):
                return "sky-night"
            return "sky-day" if "Sky" in name else "scene-outdoor"
        if kind == "decals":
            return "decals"
        if kind in ("atlases", "brushes"):
            return "atlases"
        if kind == "models":
            return "food" if set(words_of(name).split()) & set(FOOD.split()) else "nature-models"
        text = category if category and category != "More" else name
        return by_words(text)
    return OPENGAMEART.get(kind, "game-other")


def holdings(repos):
    def one(repo):
        try:
            return repo, set(json.loads(get(f"https://raw.githubusercontent.com/{ORG}/{repo}/main/.mirror.json",
                                             tries=3)).get("assets", {}))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return repo, set()
            raise
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        return dict(pool.map(one, repos))


def collect():
    items, repos = [], {}
    for source, title in SOURCES:
        plan = load_plan(source)
        for repo in plan["repos"]:
            repos[repo] = source
        for aid, a in plan["assets"].items():
            meta = a.get("meta", {})
            items.append({"source": source, "source_title": title, "id": aid, "repo": a["repo"],
                          "name": meta.get("name") or aid, "page": meta.get("page", ""),
                          "thumb_ext": meta.get("thumb_ext", "webp"), "kind": meta.get("kind", ""),
                          "topic": topic_of(source, aid, meta)})
    held = holdings(sorted(repos))
    for it in items:
        it["here"] = it["id"] in held.get(it["repo"], set())
    return items, held


def esc(text):
    return html.escape(text, quote=True)


def picture_url(it):
    return f"https://raw.githubusercontent.com/{ORG}/{it['repo']}/main/previews/{it['id']}.{it['thumb_ext']}"


def cell(it):
    folder = f"https://github.com/{ORG}/{it['repo']}/tree/main/{it['id']}"
    pic = (f'<a href="{folder}"><img src="{picture_url(it)}" width="120" alt="{esc(it["name"])}"></a><br>'
           if it["here"] else "")
    note = "" if it["here"] else "<br><i>arriving soon</i>"
    src = f' · <a href="{esc(it["page"])}">{esc(it["source_title"])}</a>' if it["page"] else f" · {esc(it['source_title'])}"
    name = f'<a href="{folder}">{esc(it["name"])}</a>' if it["here"] else esc(it["name"])
    return f'<td align="center" valign="top" width="{100 // ROW}%">{pic}<sub>{name}{src}{note}</sub></td>'


def grid(items):
    rows = []
    for i in range(0, len(items), ROW):
        rows.append("<tr>\n" + "\n".join(cell(it) for it in items[i:i + ROW]) + "\n</tr>")
    return "<table>\n" + "\n".join(rows) + "\n</table>"


def page_name(slug, n):
    return f"{slug}.md" if n == 1 else f"{slug}-{n}.md"


def write(out, items, held):
    out = Path(out)
    (out / "topics").mkdir(parents=True, exist_ok=True)
    for old in (out / "topics").glob("*.md"):
        old.unlink()
    by_topic = {}
    for it in items:
        by_topic.setdefault(it["topic"], []).append(it)
    today = datetime.date.today().isoformat()
    catalog = {"updated": today, "org": ORG, "sections": []}
    readme = [
        "# CC0 catalogue",
        "",
        f"**Every asset mirrored in [{ORG}](https://github.com/{ORG}), grouped by what it is.** "
        f"{len(items):,} assets from {', '.join(t for s, t in SOURCES if any(i['source'] == s for i in items))}, "
        f"all public domain under [CC0](https://creativecommons.org/publicdomain/zero/1.0/): use them in any game, "
        "free or paid, no credit needed.",
        "",
        "Each topic page shows every asset with a picture; click one to open its folder, where every file can be "
        "opened and saved on its own. The mirror repositories fill themselves, so a few assets may still be arriving. "
        f"Updated every day from [cc0-mirror](https://github.com/{CODE_REPO}) (last: {today}).",
        "",
    ]
    for sec, sec_title, topics in SECTIONS:
        cards, sec_json = [], {"slug": sec, "title": sec_title, "topics": []}
        for slug, title, about in topics:
            its = sorted(by_topic.get(slug, []), key=lambda i: (not i["here"], i["name"].lower(), i["source"]))
            if not its:
                continue
            here = [i for i in its if i["here"]]
            sources = sorted({i["source_title"] for i in its})
            pages = (len(its) + PAGE - 1) // PAGE
            topic_repos = sorted({i["repo"] for i in its})
            pick = here[len(here) // 3] if here else None
            sec_json["topics"].append({
                "slug": slug, "title": title, "about": about, "assets": len(its), "here": len(here),
                "sources": sources, "repos": topic_repos, "page": f"topics/{slug}.md",
                "picture": picture_url(pick) if pick else None})
            for n in range(1, pages + 1):
                chunk = its[(n - 1) * PAGE:n * PAGE]
                nav = " · ".join(f"**{k}**" if k == n else f"[{k}]({page_name(slug, k)})" for k in range(1, pages + 1))
                body = [f"# {title}", "", f"{about}. **{len(its):,} assets** from {', '.join(sources)}"
                        + (f", {len(its) - len(here):,} still arriving." if len(here) < len(its) else "."), "",
                        f"[← All topics](../README.md)" + (f" · Page {nav}" if pages > 1 else ""), ""]
                if n == 1:
                    rows = []
                    for repo in topic_repos:
                        count = sum(1 for i in its if i["repo"] == repo)
                        rows.append(f"| [{repo}](https://github.com/{ORG}/{repo}) | {count:,} |")
                    body += ["<details>", f"<summary>In {len(topic_repos)} repositories</summary>", "",
                             "| Repository | Assets in this topic |", "| --- | --: |", *rows, "", "</details>", ""]
                body += [grid(chunk), ""]
                if pages > 1:
                    body += [f"Page {nav}", ""]
                body += ["---", "", "CC0: no credit needed, but please support the people who make these: "
                         "[Poly Haven](https://www.patreon.com/polyhaven/overview), "
                         "[ambientCG](https://www.patreon.com/ambientCG) and the artists on "
                         "[OpenGameArt](https://opengameart.org). The sources are not affiliated with this catalogue.",
                         ""]
                (out / "topics" / page_name(slug, n)).write_text("\n".join(body))
            pic = (f'<a href="topics/{slug}.md"><img src="{picture_url(pick)}" width="140" alt=""></a><br>'
                   if pick else "")
            cards.append(f'<td align="center" valign="top" width="25%">{pic}<b><a href="topics/{slug}.md">'
                         f'{esc(title)}</a></b><br><sub>{len(its):,} assets · {", ".join(sources)}</sub></td>')
        if not cards:
            continue
        catalog["sections"].append(sec_json)
        rows = ["<tr>\n" + "\n".join(cards[i:i + 4]) + "\n</tr>" for i in range(0, len(cards), 4)]
        readme += [f"## {sec_title}", "", "<table>\n" + "\n".join(rows) + "\n</table>", ""]
    readme += ["## By source", "", "| Source | Assets | Repositories | Filled |", "| --- | --: | --: | --: |"]
    for source, title in SOURCES:
        plan_repos = sorted(r for r in held if load_plan(source)["repos"].get(r))
        if not plan_repos:
            continue
        its = [i for i in items if i["source"] == source]
        done = sum(1 for r in plan_repos if {i["id"] for i in its if i["repo"] == r} <= held[r] and held[r])
        readme.append(f"| [{title}](https://github.com/orgs/{ORG}/repositories?q={source}-) | {len(its):,} | "
                      f"{len(plan_repos)} | {done} |")
    readme += ["", "---", "", "The list is CC0 too. Something wrong or missing? "
               f"[Open an issue in cc0-mirror](https://github.com/{CODE_REPO}/issues/new).", ""]
    catalog["assets"] = len(items)
    catalog["here"] = sum(1 for i in items if i["here"])
    (out / "README.md").write_text("\n".join(readme))
    (out / "catalog.json").write_text(json.dumps(catalog, indent=1) + "\n")
    return catalog


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--setup", action="store_true", help="also write LICENSE and .github/workflows/update.yml")
    opts = ap.parse_args()
    need_org()
    if opts.setup:
        template = ROOT / "mirror" / "template-catalog"
        for f in ("LICENSE", ".github/workflows/update.yml"):
            (opts.out / f).parent.mkdir(parents=True, exist_ok=True)
            (opts.out / f).write_text((template / f).read_text().replace("@MIRROR_REPO@", CODE_REPO))
    items, held = collect()
    catalog = write(opts.out, items, held)
    print(f"catalogue: {catalog['assets']:,} assets ({catalog['here']:,} mirrored), "
          f"{sum(len(s['topics']) for s in catalog['sections'])} topics")


if __name__ == "__main__":
    main()
