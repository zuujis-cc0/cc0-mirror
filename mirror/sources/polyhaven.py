"""Poly Haven (https://polyhaven.com): CC0 textures, models and HDRI skies, through its public API.

What is mirrored is the part that suits games, not every file Poly Haven offers (that is about 8.5 TB):
textures as 1K and 2K JPG maps, models as 1K and 2K glTF (with their JPG textures), HDRIs as 1K and 2K .hdr.
The API's terms (github.com/Poly-Haven/Public-API/blob/master/ToS.md) ask for a User-Agent naming the client,
which common.UA does, and no heavy load, which the small thread pools keep to.
"""
import posixpath

from mirror.common import get_json

NAME = "polyhaven"
TITLE = "Poly Haven"
SITE = "https://polyhaven.com"
LICENSE_URL = "https://polyhaven.com/license"
SUPPORT_URL = "https://www.patreon.com/polyhaven/overview"
API = "https://api.polyhaven.com"
KINDS = {0: "hdris", 1: "textures", 2: "models"}
RESOLUTIONS = ("1k", "2k")
# Map keys of a texture that are not maps.
NOT_MAPS = {"blend", "gltf", "mtlx", "usd", "fbx"}
ABOUT = {
    "textures": "PBR textures as 1K and 2K JPG maps (colour, normal (GL and DX), roughness, AO, displacement and more).",
    "models": "3D models as glTF at 1K and 2K texture resolution, each folder self-contained (`.gltf`, `.bin`, `textures/`).",
    "hdris": "HDRI skies and environments as 1K and 2K Radiance `.hdr` images, for lighting and backgrounds.",
}


def catalogue():
    """Every asset: id -> what plan.py and sync.py need."""
    out = {}
    for aid, a in get_json(f"{API}/assets").items():
        kind = KINDS.get(a.get("type"))
        if not kind:
            continue
        top = (a.get("category") or (a.get("categories") or ["other"])[0]).split("/")[0]
        out[aid] = {
            "kind": kind,
            "group": f"{kind}-{top}",
            "category": top,
            "name": a.get("name") or aid,
            "authors": sorted(a.get("authors", {})),
            "hash": a.get("files_hash") or "",
            "page": f"{SITE}/a/{aid}",
            "thumb": (a.get("thumbnail_url") or f"https://cdn.polyhaven.com/asset_img/thumbs/{aid}.png?width=256&height=256&format=webp")
            .replace("width=256", "width=192").replace("height=256", "height=192"),
        }
    return out


def repo_base(group):
    from mirror.common import slug

    return f"polyhaven-{slug(group)}"


def _file(path, node):
    return {"path": path, "url": node["url"], "size": node["size"], "md5": node.get("md5", "")}


def files(aid, meta):
    """The files of one asset that are mirrored: dicts of path (in the repository), url, size, md5."""
    tree = get_json(f"{API}/files/{aid}")
    out = []
    kind = meta["kind"]
    if kind == "hdris":
        for res in RESOLUTIONS:
            node = tree.get("hdri", {}).get(res, {}).get("hdr")
            if node:
                out.append(_file(f"{aid}/{posixpath.basename(node['url'])}", node))
    elif kind == "models":
        for res in RESOLUTIONS:
            node = tree.get("gltf", {}).get(res, {}).get("gltf")
            if not node:
                continue
            out.append(_file(f"{aid}/{res}/{posixpath.basename(node['url'])}", node))
            for rel, inc in node.get("include", {}).items():
                out.append(_file(posixpath.normpath(f"{aid}/{res}/{rel}"), inc))
    else:
        for key, maps in tree.items():
            if key in NOT_MAPS or not isinstance(maps, dict):
                continue
            for res in RESOLUTIONS:
                node = maps.get(res, {}).get("jpg")
                if node:
                    out.append(_file(f"{aid}/{res}/{posixpath.basename(node['url'])}", node))
    # One entry per path (a texture can appear under two map keys).
    return list({f["path"]: f for f in out}.values())
