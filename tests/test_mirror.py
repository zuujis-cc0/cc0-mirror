"""Unit tests: python3 -m unittest discover -s tests (no network)."""
import json
import struct
import tempfile
import unittest
from pathlib import Path

from mirror import gltf_split, plan, sync
from mirror.common import REPO_LIMIT, slug


class Src:
    NAME = "demo"

    @staticmethod
    def repo_base(group):
        return f"demo-{slug(group)}"


class SplitTest(unittest.TestCase):
    def test_views_survive_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            views, blob = [], bytearray()
            for i, n in enumerate((30, 50, 7, 41)):
                blob += b"\0" * (-len(blob) % 4)
                views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": n})
                blob += bytes([i + 1]) * n
            (d / "m.bin").write_bytes(blob)
            (d / "m.gltf").write_text(json.dumps({"buffers": [{"uri": "m.bin", "byteLength": len(blob)}], "bufferViews": views}))
            old_part = gltf_split.PART
            gltf_split.PART = 60
            try:
                self.assertTrue(gltf_split.needs_split(d / "m.gltf", 40))
                removed, added = gltf_split.split(d / "m.gltf", 40)
            finally:
                gltf_split.PART = old_part
            self.assertEqual(removed, ["m.bin"])
            self.assertGreater(len(added), 1)
            self.assertFalse((d / "m.bin").exists())
            new = json.loads((d / "m.gltf").read_text())
            for i, v in enumerate(new["bufferViews"]):
                data = (d / new["buffers"][v["buffer"]]["uri"]).read_bytes()
                self.assertEqual(v["byteOffset"] % 4, 0)
                self.assertEqual(data[v["byteOffset"]:v["byteOffset"] + v["byteLength"]], bytes([i + 1]) * v["byteLength"])
            for b in new["buffers"]:
                self.assertEqual((d / b["uri"]).stat().st_size, b["byteLength"])
                self.assertLessEqual(b["byteLength"], 60)

    def test_a_view_too_big_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "m.bin").write_bytes(b"x" * 100)
            (d / "m.gltf").write_text(json.dumps({"buffers": [{"uri": "m.bin", "byteLength": 100}],
                                                  "bufferViews": [{"buffer": 0, "byteLength": 100}]}))
            old_part = gltf_split.PART
            gltf_split.PART = 60
            try:
                with self.assertRaises(ValueError):
                    gltf_split.split(d / "m.gltf", 40)
            finally:
                gltf_split.PART = old_part


class PlanTest(unittest.TestCase):
    def test_assets_fill_a_repository_then_start_the_next(self):
        p = {"repos": {}, "assets": {}, "skipped": {}}
        meta = {"group": "textures-Wood", "category": "Wood", "kind": "textures"}
        first = plan.assign(p, Src, "a", meta, REPO_LIMIT - 10)
        second = plan.assign(p, Src, "b", meta, 5)
        third = plan.assign(p, Src, "c", meta, 50)
        fourth = plan.assign(p, Src, "d", meta, 50)
        self.assertEqual((first, second, third, fourth), ("demo-textures-wood",) * 2 + ("demo-textures-wood-2",) * 2)
        self.assertEqual(p["repos"]["demo-textures-wood"]["bytes"], REPO_LIMIT - 5)

    def test_tenth_part_sorts_after_ninth(self):
        p = {"repos": {}, "assets": {}, "skipped": {}}
        meta = {"group": "g", "category": "G", "kind": "models"}
        names = [plan.assign(p, Src, f"a{i}", meta, REPO_LIMIT) for i in range(11)]
        self.assertEqual(names[-1], "demo-g-11")

    def test_shared_files_count_once(self):
        files = [{"path": "x/1k/m.bin", "size": 10, "md5": "same"}, {"path": "x/2k/m.bin", "size": 10, "md5": "same"},
                 {"path": "x/1k/t.jpg", "size": 3, "md5": "other"}]
        self.assertEqual(plan.asset_bytes(files), 13)


class TemplateTest(unittest.TestCase):
    def test_update_workflow_is_filled_in(self):
        class S:
            NAME, TITLE = "polyhaven", "Poly Haven"

        text = sync.render_update(S, "polyhaven-models-nature-2")
        self.assertNotIn("@", text.replace("actions/checkout@", ""))
        self.assertIn("python3 -m mirror.sync polyhaven polyhaven-models-nature-2 --self", text)
        self.assertIn(f"repository: {sync.CODE_REPO}", text)
        minute, hour, _, _, dow = sync.cron_for("polyhaven-models-nature-2").split()
        self.assertTrue(0 <= int(minute) < 60 and 0 <= int(hour) < 24 and 0 <= int(dow) < 7)


class CatalogTest(unittest.TestCase):
    def test_topics_group_across_sources(self):
        from mirror.catalog import TOPICS, topic_of
        cases = [
            ("polyhaven", "wood_floor", {"kind": "textures", "category": "Wood"}, "wood"),
            ("ambientcg", "WoodFloor041", {"kind": "materials", "category": "Wood Floor"}, "wood"),
            ("ambientcg", "WoodFloorSubstance009", {"kind": "substances", "category": ""}, "wood"),
            ("ambientcg", "PaintedMetal004", {"kind": "materials", "category": "Painted Metal"}, "metal"),
            ("ambientcg", "PavingStones130", {"kind": "materials", "category": "Paving Stones"}, "paving"),
            ("ambientcg", "Onyx010", {"kind": "materials", "category": "More"}, "stone"),
            ("ambientcg", "Fingerprints005", {"kind": "materials", "category": "More"}, "wear"),
            ("ambientcg", "EveningSkyHDRI031", {"kind": "hdris", "category": ""}, "sky-night"),
            ("ambientcg", "IndoorEnvironmentHDRI004", {"kind": "hdris", "category": ""}, "scene-indoor"),
            ("ambientcg", "3DBread004", {"kind": "models", "category": ""}, "food"),
            ("polyhaven", "kloppenheim", {"kind": "hdris", "category": "Pure Skies"}, "sky-day"),
            ("polyhaven", "ArmChair_01", {"kind": "models", "category": "Furniture"}, "furniture"),
            ("opengameart", "tiny-town", {"kind": "2d-art"}, "art-2d"),
        ]
        for source, aid, meta, want in cases:
            self.assertEqual(topic_of(source, aid, meta), want, aid)
            self.assertIn(want, TOPICS)


if __name__ == "__main__":
    unittest.main()
