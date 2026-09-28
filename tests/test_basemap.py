"""The ground under the live map (n23, ADR 0019).

Two promises, tested separately because they fail separately:

- It lines up with the pins BY CONSTRUCTION: one projector places both, so a
  basemap vertex at a town's coordinates lands on that town's pin.
- It is only as good as its data, and is never drawn finer than that: the
  tiers, their tolerances and the page's limit are the build's numbers.

And the rules the map already had: nothing reaches the network, missing data
is a flat map rather than an error, and the basemap decides nothing.
"""

import gzip
import importlib.util
import json
import math
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jsa import basemap, doctor, mapview, places, web
from tests.test_map import PROFILE, SPREAD, home

ROOT = Path(__file__).resolve().parent.parent


def build_tool():
    spec = importlib.util.spec_from_file_location(
        "build_map_data", ROOT / "tools" / "build_map_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def place(name, state):
    return places.resolve(name, state)


def moves(path_d):
    """Absolute (x, y) of every vertex in an 'M x y l dx dy ...' path."""
    out = []
    for piece in re.findall(r"M[^M]*", path_d):
        nums = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?(?:e-?\d+)?", piece)]
        x, y = nums[0], nums[1]
        out.append((x, y))
        for i in range(2, len(nums) - 1, 2):
            x, y = x + nums[i], y + nums[i + 1]
            out.append((x, y))
    return out


class FixtureData(unittest.TestCase):
    """Point the module at tier files written here, and put it back."""

    Q = 10000

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        patcher = mock.patch.object(basemap, "DATA", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        for cache in (basemap._tier, basemap._layers):
            cache.cache_clear()
            self.addCleanup(cache.cache_clear)

    def feature(self, points):
        tool = build_tool()
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        return [round(min(xs) * self.Q), round(min(ys) * self.Q),
                round(max(xs) * self.Q), round(max(ys) * self.Q),
                tool._ring_flat(points, self.Q)]

    def write(self, layers, tiers=basemap.TIERS):
        for tier in tiers:
            payload = {"tier": tier, "ppm": basemap.PPM[tier], "q": self.Q,
                       "tolerance_deg": 0.0, "layers": layers}
            with gzip.open(basemap.path(tier), "wt", encoding="utf-8") as fh:
                json.dump(payload, fh)


class TestOneProjection(FixtureData):
    def test_pins_and_ground_call_the_same_function(self):
        """Not 'produce the same numbers': the SAME function, so that a
        change to one is a change to both."""
        town = place("Nampa", "ID")
        self.write({"road": [self.feature([(town.lon, town.lat), (town.lon + .1, town.lat)])]})
        calls = []
        real = mapview.projector

        def spy(origin):
            calls.append(origin)
            return real(origin)

        with mock.patch.object(mapview, "projector", spy):
            mapview.points(SPREAD, home())
            basemap.layers(home(), "fine")
        self.assertEqual(calls, [home(), home()])

    def test_projector_is_the_filters_geometry(self):
        """hypot is the great-circle distance and the angle is the bearing,
        so the ground cannot drift from the circle either."""
        for origin in (home(), place("Seattle", "WA"), place("Miami", "FL")):
            project = mapview.projector(origin)
            for other in (place("Salt Lake City", "UT"), place("Portland", "OR"),
                          place("New York", "NY"), origin):
                x, y = project(other.lat, other.lon)
                with self.subTest(origin=str(origin), other=str(other)):
                    self.assertAlmostEqual(math.hypot(x, y), places.miles(origin, other),
                                           delta=1e-6)
                    if other != origin:
                        self.assertAlmostEqual(math.atan2(x, y),
                                               mapview.bearing(origin, other), delta=1e-9)

    def test_unproject_undoes_it(self):
        origin = place("Seattle", "WA")
        project = mapview.projector(origin)
        for x, y in ((0, 0), (120.5, -40.25), (-900, 300), (2000, 1500)):
            lat, lon = mapview.unproject(origin, x, y)
            back = project(lat, lon)
            self.assertAlmostEqual(back[0], x, delta=1e-6)
            self.assertAlmostEqual(back[1], y, delta=1e-6)


class TestAVertexAtATownIsOnItsPin(FixtureData):
    """The whole of "lines up perfectly", as far as code can promise it."""

    TOWNS = [("Meridian", "ID"), ("Nampa", "ID"), ("Salt Lake City", "UT"),
             ("Seattle", "WA"), ("Portland", "OR")]

    def test_for_origins_in_three_states(self):
        towns = [place(n, s) for n, s in self.TOWNS]
        # One tiny ring per town, first vertex exactly on the town's point.
        self.write({"land": [self.feature([(t.lon, t.lat), (t.lon + .01, t.lat),
                                           (t.lon + .01, t.lat + .01), (t.lon, t.lat)])
                             for t in towns]})
        rows = [{"job_id": i, "location": f"{n}, {s}", "remote": "onsite"}
                for i, (n, s) in enumerate(self.TOWNS)]
        # Boise is east of Seattle and Portland, west of Salt Lake City:
        # both signs of x, and both of y, are exercised.
        for origin in (home(), place("Seattle", "WA"), place("Salt Lake City", "UT")):
            basemap._layers.cache_clear()
            pins = {p["place"]: (p["x"], p["y"])
                    for p in mapview.points(rows, origin)[0]}
            ground = basemap.layers(origin, "fine", reach=2000)["layers"]["land"]
            firsts = [moves(piece)[0] for piece in re.findall(r"M[^M]*", ground)]
            for (name, _), vertex in zip(self.TOWNS, firsts):
                with self.subTest(origin=str(origin), town=name):
                    pin = pins[name]
                    self.assertLess(math.hypot(vertex[0] - pin[0], vertex[1] - pin[1]),
                                    0.001 + 1e-9)


class TestClipping(FixtureData):
    def test_outside_is_absent_and_crossing_is_kept(self):
        near = place("Nampa", "ID")
        far = place("Miami", "FL")
        self.write({"road": [
            self.feature([(far.lon, far.lat), (far.lon + .2, far.lat)]),
            # From well inside the box to far outside it.
            self.feature([(near.lon, near.lat), (near.lon + 30, near.lat)]),
        ]})
        got = basemap.layers(home(), "fine")["layers"]["road"]
        self.assertEqual(got.count("M"), 1)
        x, _ = moves(got)[0]
        self.assertAlmostEqual(x, mapview.projector(home())(near.lat, near.lon)[0], delta=0.001)

    def test_coarse_is_the_whole_country(self):
        far = place("Miami", "FL")
        self.write({"road": [self.feature([(far.lon, far.lat), (far.lon + .2, far.lat)])]})
        self.assertEqual(basemap.layers(home(), "coarse")["layers"]["road"].count("M"), 1)


class TestTheTiersAreTheBuildsNumbers(unittest.TestCase):
    def test_the_page_and_the_build_agree(self):
        tool = build_tool()
        self.assertEqual({t: v["ppm"] for t, v in tool.TIERS.items()}, basemap.PPM)
        self.assertEqual(tuple(tool.TIERS), basemap.TIERS)

    @unittest.skipUnless(basemap.available(), "no shipped basemap")
    def test_the_shipped_files_were_built_for_these_scales(self):
        for tier in basemap.TIERS:
            with gzip.open(basemap.path(tier), "rt", encoding="utf-8") as fh:
                head = fh.read(200)
            with self.subTest(tier=tier):
                self.assertIn(f'"ppm":{basemap.PPM[tier]}', head)

    def test_simplification_stays_within_half_a_pixel(self):
        """A wiggly line through the tier's own simplifier: no raw point ends
        up further from the kept line than the tier's tolerance, and the
        stored integers do not add more than their own half-step."""
        tool = build_tool()
        raw = [(-116 + i * 0.001, 43.6 + 0.004 * math.sin(i / 3.0)) for i in range(400)]
        for tier, spec in tool.TIERS.items():
            tolerance = 0.5 / spec["ppm"] / tool.MILES_PER_DEGREE
            feature = tool._features([raw], tolerance, spec["q"], closed=False)[0]
            flat, pts, x, y = feature[4], [], 0, 0
            for i in range(0, len(flat), 2):
                x, y = x + flat[i], y + flat[i + 1]
                pts.append((x / spec["q"], y / spec["q"]))
            worst = 0.0
            for px, py in raw:
                best = min(_seg_dist(px, py, a, b) for a, b in zip(pts, pts[1:]))
                worst = max(worst, best)
            with self.subTest(tier=tier):
                self.assertLessEqual(worst, tolerance + 1 / spec["q"])

    def test_a_speck_is_left_out(self):
        tool = build_tool()
        tolerance = 0.5 / 0.8 / tool.MILES_PER_DEGREE
        side = 2 * tolerance            # survives simplification, under 2 px across
        tiny = [(-116, 43), (-116 + side, 43), (-116 + side, 43 + side), (-116, 43)]
        self.assertEqual(tool._features([tiny], tolerance, 500, closed=True), [])


def _seg_dist(px, py, a, b):
    (x1, y1), (x2, y2) = a, b
    dx, dy = x2 - x1, y2 - y1
    span = dx * dx + dy * dy
    t = 0 if span == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / span))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


class TestTownsOffTheirOwnLand(unittest.TestCase):
    """A Census internal point is inside the town's area including its
    water. San Francisco's was 30 miles out to sea, off the Farallones."""

    SQUARE = [[(-122.5, 37.7), (-122.4, 37.7), (-122.4, 37.8), (-122.5, 37.8), (-122.5, 37.7)]]

    def test_a_point_far_off_its_land_moves_onto_it(self):
        tool = build_tool()
        row = "CA\t0667000\t1\tSan Francisco city\t25\tA\t1\t1\t1\t1\t37.72\t-123.03"
        rows = tool.build_places(("h\n" + row + "\n").encode(), {"0667000": self.SQUARE})
        (_, _, lat, lon), = rows
        self.assertTrue(tool._inside(self.SQUARE[0], lon, lat))

    def test_a_point_just_off_the_shore_stays(self):
        """Within the boundary file's own error: moving it would be moving
        a town for nothing, and every distance to it with it."""
        tool = build_tool()
        row = "CA\t0667000\t1\tSan Francisco city\t25\tA\t1\t1\t1\t1\t37.75\t-122.501"
        rows = tool.build_places(("h\n" + row + "\n").encode(), {"0667000": self.SQUARE})
        self.assertEqual(rows[0][2:], (37.75, -122.501))

    def test_a_point_on_a_c_shaped_town_is_still_on_it(self):
        tool = build_tool()
        c_shape = [(0, 0), (3, 0), (3, 1), (1, 1), (1, 2), (3, 2), (3, 3), (0, 3), (0, 0)]
        x, y = tool.point_on_land([c_shape])
        self.assertTrue(tool._inside(c_shape, x, y))

    @unittest.skipUnless(places.data_is_present(), "no shipped place data")
    def test_the_shipped_san_francisco_is_on_land(self):
        sf = place("San Francisco", "CA")
        self.assertGreater(sf.lon, -122.55)     # the city, not the Farallones


class TestThePage(unittest.TestCase):
    def setUp(self):
        from tests.test_live_map import TestThePage as Live

        live = Live()
        live.setUp()
        self.addCleanup(live.doCleanups)
        self.client, self.live = live.client, live

    def test_the_page_says_where_its_ground_comes_from(self):
        data = self.live.data(self.live.page())
        if basemap.available():
            self.assertEqual(set(data["basemap"]), {"home", "ppm", "reach"})
            self.assertEqual(data["basemap"]["ppm"], basemap.PPM)
        else:
            self.assertIsNone(data["basemap"])

    @unittest.skipUnless(basemap.available(), "no shipped basemap")
    def test_the_route_serves_geography_and_nothing_else(self):
        r = self.client.get("/basemap/fine?x=0&y=0")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(set(body), {"tier", "ppm", "reach", "at", "layers"})
        self.assertLessEqual(set(body["layers"]), set(basemap.LAYERS))
        self.assertNotIn("Near", r.text)            # no posting titles

    def test_the_route_refuses_nonsense(self):
        self.assertEqual(self.client.get("/basemap/street").status_code, 404)
        self.assertEqual(self.client.get("/basemap/fine?x=nan").status_code, 400)
        self.assertEqual(self.client.get("/basemap/fine?x=inf").status_code, 400)

    def test_the_route_is_host_checked_like_every_other(self):
        r = self.client.get("/basemap/coarse", headers={"host": "evil.test"})
        self.assertNotEqual(r.status_code, 200)

    def test_the_ground_is_hidden_from_assistive_tech_and_tab_order(self):
        text = self.live.page()
        self.assertIn("node('canvas', {'class': 'bm-canvas', 'aria-hidden': 'true'}, null)", text)
        self.assertIn("pointer-events:none", text)


class TestMissingData(FixtureData):
    """No files: a flat map, exactly as before n23, and doctor says so."""

    def test_layers_is_none_and_nothing_raises(self):
        self.assertFalse(basemap.available())
        self.assertIsNone(basemap.layers(home(), "fine"))

    def test_the_page_still_draws_its_map(self):
        from tests.test_live_map import TestThePage as Live

        live = Live()
        live.setUp()
        self.addCleanup(live.doCleanups)
        text = live.page()
        data = live.data(text)
        self.assertIsNone(data["basemap"])
        self.assertEqual(len(data["points"]), 9)
        self.assertEqual(live.client.get("/basemap/fine").status_code, 404)

    def test_the_static_map_is_flat(self):
        view = mapview.local(SPREAD, home(), 25)
        self.assertEqual(view.ground, {})

    def test_doctor_reports_it(self):
        report = doctor.Report()
        doctor.check_basemap(report)
        self.assertEqual(len(report.advisory), 1)
        self.assertFalse(report.blocking)
        self.assertIn("--only basemap", report.advisory[0].fix)


class TestTheStaticMapHasTheSameGround(FixtureData):
    def test_same_data_same_projector(self):
        town = place("Nampa", "ID")
        self.write({"land": [self.feature([(town.lon, town.lat), (town.lon + .05, town.lat),
                                           (town.lon + .05, town.lat + .05), (town.lon, town.lat)])]})
        view = mapview.local(SPREAD, home(), 25)
        self.assertIn("land", view.ground)
        x, y = moves(view.ground["land"])[0]
        px, py = mapview.projector(home())(town.lat, town.lon)
        self.assertLess(math.hypot(x - px, y - py), 0.001 + 1e-9)

    def test_none_past_the_finest_scale(self):
        self.write({"land": []})
        view = mapview.local(SPREAD, home(), 5)          # about 27 px per mile
        self.assertGreater(view.per_mile, basemap.PPM["fine"])
        self.assertEqual(view.ground, {})


class TestTheBasemapDecidesNothing(unittest.TestCase):
    def test_points_are_the_same_with_and_without_it(self):
        before = mapview.points(SPREAD, home())
        with mock.patch.object(basemap, "available", lambda: False):
            after = mapview.points(SPREAD, home())
        self.assertEqual(before, after)


class TestReproducible(unittest.TestCase):
    def test_the_same_tier_twice_is_the_same_bytes(self):
        tool = build_tool()
        ring = [(-116.2, 43.6), (-116.1, 43.6), (-116.1, 43.7), (-116.2, 43.6)]
        raw = {"land": [ring], "county": [ring], "urban": [ring], "lake": [ring],
               "road": [ring[:2]], "road_i": [ring[:2]]}
        out = []
        for _ in range(2):
            path = Path(tempfile.mkdtemp())
            self.addCleanup(shutil.rmtree, path, True)
            payload = json.dumps(tool.build_basemap_tier("fine", raw),
                                 separators=(",", ":")).encode()
            out.append(tool.write_gz(path / "t.json.gz", payload))
        self.assertEqual(out[0], out[1])


if __name__ == "__main__":
    unittest.main()
