"""Canada's ground under the live map (ADR 0037, 2026-11-07).

Built by tools/build_ca_data.py from Statistics Canada's boundary and road
files, in the US basemap's exact shape: the same tiers, scales, layers and
simplification (tools/build_map_data.py, ADR 0019), so the page draws it the
same way and nothing in jsa/ knows which country it came from.
"""

import gzip
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jsa import basemap, countries, doctor, places

ROOT = Path(__file__).resolve().parent.parent


def tool():
    sys.path.insert(0, str(ROOT / "tools"))
    spec = importlib.util.spec_from_file_location(
        "build_ca_data", ROOT / "tools" / "build_ca_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def shipped(country, tier):
    with gzip.open(ROOT / "jsa" / "resources" / "data" / country.lower()
                   / f"{country.lower()}_basemap_{tier}.json.gz", "rt", encoding="utf-8") as fh:
        return json.load(fh)


class InCanada(unittest.TestCase):
    def setUp(self):
        self.addCleanup(countries.activate, countries.DEFAULT)
        countries.activate("CA")
        basemap._layers.cache_clear()
        self.addCleanup(basemap._layers.cache_clear)


class TestTheShippedFiles(InCanada):
    def test_every_tier_ships(self):
        self.assertTrue(basemap.available())
        self.assertEqual(basemap.path("coarse").parent.name, "ca")

    def test_same_scales_and_layers_as_the_us(self):
        for tier in basemap.TIERS:
            ca, us = shipped("CA", tier), shipped("US", tier)
            with self.subTest(tier=tier):
                self.assertEqual((ca["ppm"], ca["q"], ca["tolerance_deg"]),
                                 (us["ppm"], us["q"], us["tolerance_deg"]))
                self.assertEqual(set(ca["layers"]), set(us["layers"]))
                self.assertTrue(all(ca["layers"][layer] for layer in ca["layers"]))

    def test_every_city_name_stands_on_a_town(self):
        cities = shipped("CA", "fine")["cities"]
        self.assertIn("Montreal", [c[0] for c in cities])
        missing = [f"{c[0]}, {c[1]}" for c in cities if places.resolve(c[0], c[1]) is None]
        self.assertEqual(missing, [])

    def test_toronto_has_land_water_roads_and_its_name(self):
        toronto = places.resolve("Toronto", "ON")
        for tier in ("medium", "fine"):
            got = basemap.layers(toronto, tier)
            with self.subTest(tier=tier):
                for layer in ("land", "lake", "road", "urban"):
                    self.assertTrue(got["layers"][layer], layer)
                self.assertIn("Toronto", [c[0] for c in got["cities"]])

    def test_the_us_still_draws_its_own(self):
        countries.activate("US")
        self.assertEqual(basemap.path("coarse").parent.name, "us")


class TestTheBuild(unittest.TestCase):
    def test_holes_are_lakes_and_outer_rings_are_land(self):
        t = tool()
        outer = [(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)]     # clockwise, Esri's outer
        hole = [(2, 2), (4, 2), (4, 4), (2, 4), (2, 2)]          # anticlockwise, a hole
        self.assertLess(t._signed(outer), 0)
        self.assertGreater(t._signed(hole), 0)

    def test_a_lake_the_boundary_file_cut_out_is_not_drawn_twice(self):
        t = tool()
        land = t._boxed([[(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)]])
        holes = t._boxed([[(2, 2), (4, 2), (4, 4), (2, 4), (2, 2)]])
        self.assertTrue(t._in_any(land, 3, 3) and t._in_any(holes, 3, 3))
        self.assertTrue(t._in_any(land, 7, 7) and not t._in_any(holes, 7, 7))

    def test_tiers_are_the_us_tools_own(self):
        t = tool()
        self.assertEqual({k: v["ppm"] for k, v in t.TIERS.items()}, basemap.PPM)

    def test_the_aliases_name_real_towns(self):
        t = tool()
        self.addCleanup(countries.activate, countries.DEFAULT)
        countries.activate("CA")
        for (_, prov), name in t.CITY_ALIASES.items():
            with self.subTest(name=name):
                self.assertIsNotNone(places.resolve(name, prov))


class TestDoctorNamesTheRightTool(InCanada):
    def test_missing_canadian_files_point_at_the_canada_build(self):
        with tempfile.TemporaryDirectory() as empty, \
                mock.patch.object(basemap, "DATA", Path(empty) / "ca"):
            report = doctor.Report()
            doctor.check_basemap(report)
        fix = report.advisory[0].fix
        self.assertIn("data/ca/", fix)
        self.assertIn("tools/build_ca_data.py --only basemap", fix)


if __name__ == "__main__":
    unittest.main()
