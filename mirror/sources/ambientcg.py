"""ambientCG (https://ambientcg.com): CC0 PBR materials, HDRIs, decals, atlases and models, through its public API.

What is mirrored is the part that suits games: 1K and 2K JPG maps (unpacked from ambientCG's ZIPs into a folder per
asset and resolution), 1K and 2K HDRIs, the low-poly ("LQ") 1K and 2K JPG versions of models, and compiled
Substance files. About 73 GB of ambientCG's 4.2 TB. robots.txt allows the API; requests carry common.UA.
"""
import hashlib
import json
from collections import defaultdict

from mirror.common import get_json, slug

NAME = "ambientcg"
TITLE = "ambientCG"
SITE = "https://ambientcg.com"
LICENSE_URL = "https://docs.ambientcg.com/license/"
SUPPORT_URL = "https://www.patreon.com/ambientCG"
API = "https://ambientcg.com/api/v2/full_json"
THREADS = 3
MAX_PARALLEL = 3
KINDS = {
    "Material": "materials", "HDRI": "hdris", "Decal": "decals", "Atlas": "atlases", "3DModel": "models",
    "Substance": "substances", "Terrain": "terrains", "PlainTexture": "plain-textures", "Brush": "brushes",
    "HDRIElement": "hdri-elements",
}
KIND_TITLES = {
    "materials": "materials", "hdris": "HDRIs", "decals": "decals", "atlases": "atlases", "models": "models",
    "substances": "Substance materials", "terrains": "terrains", "plain-textures": "plain textures",
    "brushes": "brushes", "hdri-elements": "HDRI elements",
}
ABOUT = {
    "materials": "PBR materials as 1K and 2K JPG maps (colour, normal GL and DX, roughness, AO, displacement), one folder per resolution.",
    "hdris": "HDRI skies and environments at 1K and 2K, for lighting and backgrounds.",
    "decals": "Decals (manhole covers, stains, markings and more) as 1K and 2K JPG maps with opacity.",
    "atlases": "Texture atlases (leaves, plants and more) as 1K and 2K JPG maps with opacity.",
    "models": "Low-poly 3D models with 1K and 2K JPG textures.",
    "substances": "Compiled Substance materials (.sbsar).",
    "terrains": "Terrain height and colour maps as 1K and 2K JPG.",
    "plain-textures": "Plain textures and backdrops as 1K and 2K JPG.",
    "brushes": "Brushes as 1K and 2K JPG.",
    "hdri-elements": "HDRI elements (single objects lit for compositing) at 1K and 2K.",
}
WANT = {
    "HDRI": {"1K", "2K"}, "HDRIElement": {"1K", "2K"}, "3DModel": {"LQ-1K-JPG", "LQ-2K-JPG"}, "Substance": {"COMPILED"},
}
DEFAULT_WANT = {"1K-JPG", "2K-JPG"}
# A material category smaller than this shares a "More" repository with the other small ones.
OWN_REPO = 300_000_000


def _downloads(a):
    want = WANT.get(a["dataType"], DEFAULT_WANT)
    out = []
    for folder in (a.get("downloadFolders") or {}).values():
        cats = folder.get("downloadFiletypeCategories") or {}
        for cat in (cats.values() if isinstance(cats, dict) else cats):
            for d in cat.get("downloads", []):
                if d.get("attribute") in want and d.get("filetype") in ("zip", "sbsar") and d.get("size"):
                    out.append(d)
    return out


def catalogue():
    assets, offset = [], 0
    while True:
        page = get_json(f"{API}?include=downloadData,imageData&limit=250&offset={offset}&sort=Alphabet")
        assets += page["foundAssets"]
        offset += 250
        if offset >= page["numberOfResults"]:
            break
    sizes = defaultdict(int)
    for a in assets:
        if a["dataType"] == "Material":
            sizes[a.get("displayCategory") or "Other"] += sum(d["size"] for d in _downloads(a))
    out = {}
    for a in assets:
        kind = KINDS.get(a["dataType"])
        downloads = _downloads(a)
        if not kind or not downloads:
            continue
        if kind == "materials":
            category = a.get("displayCategory") or "Other"
            if sizes[category] < OWN_REPO:
                category = "More"
        else:
            category = "All"  # one group per kind (decals, HDRIs, ...), split into -2, -3 as it fills
        aid = a["assetId"]
        images = a.get("previewImage") or {}
        out[aid] = {
            "kind": kind,
            "group": f"{kind}-{category}",
            "category": category if category != "All" else "",
            "name": a.get("displayName") or aid,
            "authors": [],
            "hash": hashlib.sha1(json.dumps(
                [a.get("releaseDate"), sorted((d["fileName"], d["size"]) for d in downloads)]).encode()).hexdigest(),
            "page": a.get("shortLink") or f"{SITE}/view?id={aid}",
            "thumb": images.get("256-PNG") or images.get("128-PNG") or "",
            "thumb_ext": "png",
            "downloads": [{"name": d["fileName"], "url": d.get("fullDownloadPath") or d["downloadLink"], "size": d["size"],
                           "attribute": d["attribute"], "type": d["filetype"]} for d in downloads],
        }
    return out


def repo_base(group):
    return f"ambientcg-{slug(group).removesuffix('-all')}"


def files(aid, meta):
    """ZIPs are unpacked by sync.py into AID/ATTRIBUTE/; other files go in AID/ as they are."""
    out = []
    for d in meta["downloads"]:
        if d["type"] == "zip":
            out.append({"path": f"{aid}/{d['attribute']}", "url": d["url"], "size": d["size"], "unzip": True})
        else:
            out.append({"path": f"{aid}/{d['name']}", "url": d["url"], "size": d["size"]})
    return out
