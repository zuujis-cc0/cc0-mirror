"""Split a glTF model's oversized .bin into parts small enough for git, so the model still loads unchanged.

glTF lets a model use several buffers: each bufferView names its buffer and its offset in it. split() moves the
bufferViews of every buffer over the limit into new buffers of at most PART bytes (keeping each view whole and
4-byte aligned) and rewrites the .gltf to list them. Loaders that read .gltf (three.js, Godot, Blender, Unity's
glTFast) follow the buffer list, so nothing else changes. A single bufferView over the limit cannot be split this way:
split() raises, and the asset is reported instead of half-mirrored.
"""
import json
import posixpath
from pathlib import Path

PART = 90_000_000


def needs_split(gltf: Path, limit: int) -> bool:
    data = json.loads(gltf.read_text())
    return any(
        (gltf.parent / b["uri"]).exists() and (gltf.parent / b["uri"]).stat().st_size > limit
        for b in data.get("buffers", []) if "uri" in b and not b["uri"].startswith("data:")
    )


def split(gltf: Path, limit: int):
    """Rewrite gltf (and its big .bin files) in place. Returns (removed, added): paths relative to gltf's folder."""
    data = json.loads(gltf.read_text())
    buffers = data.get("buffers", [])
    removed, added = [], []
    for bi in range(len(buffers)):
        uri = buffers[bi].get("uri", "")
        src = gltf.parent / uri
        if not uri or uri.startswith("data:") or not src.exists() or src.stat().st_size <= limit:
            continue
        blob = src.read_bytes()
        views = sorted((v for v in data["bufferViews"] if v["buffer"] == bi), key=lambda v: v.get("byteOffset", 0))
        stem = posixpath.splitext(uri)[0]
        parts, cur = [], bytearray()
        for v in views:
            n = v["byteLength"]
            if n > PART:
                raise ValueError(f"{gltf.name}: a bufferView of {n} bytes is over the {PART}-byte part size")
            if cur and len(cur) + (-len(cur) % 4) + n > PART:
                parts.append(cur)
                cur = bytearray()
            cur += b"\0" * (-len(cur) % 4)
            start = v.get("byteOffset", 0)
            v["byteOffset"] = len(cur)
            cur += blob[start:start + n]
            v["_part"] = len(parts)
        parts.append(cur)
        indices = []
        for k, part in enumerate(parts):
            name = f"{stem}-part{k + 1}.bin"
            (gltf.parent / name).write_bytes(part)
            added.append(name)
            if k == 0:
                buffers[bi] = {"uri": name, "byteLength": len(part)}
                indices.append(bi)
            else:
                buffers.append({"uri": name, "byteLength": len(part)})
                indices.append(len(buffers) - 1)
        for v in views:
            v["buffer"] = indices[v.pop("_part")]
        src.unlink()
        removed.append(uri)
    gltf.write_text(json.dumps(data, separators=(",", ":")))
    return removed, added
