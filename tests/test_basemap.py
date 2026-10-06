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
from tests.web_source import web_source

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
            self.assertEqual(set(data["basemap"]), {"home", "ppm", "reach", "v"})
            self.assertEqual(data["basemap"]["v"], basemap.version())
            self.assertEqual(data["basemap"]["ppm"], basemap.PPM)
        else:
            self.assertIsNone(data["basemap"])

    @unittest.skipUnless(basemap.available(), "no shipped basemap")
    def test_the_route_serves_geography_and_nothing_else(self):
        r = self.client.get("/basemap/fine?x=0&y=0")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(set(body), {"tier", "ppm", "reach", "at", "layers", "cities"})
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


class TestTheChunkURLChangesWithTheData(FixtureData):
    """The browser may keep a chunk for a day; a rebuild must not be hidden
    behind that."""

    def test_rewriting_a_tier_changes_the_version(self):
        self.write({"road": []})
        before = basemap.version()
        self.write({"road": [], "land": []})
        self.assertNotEqual(basemap.version(), before)


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


class TestCityNames(FixtureData):
    """Names come from Natural Earth (it has population); positions come
    from the Gazetteer point a pin for that town uses."""

    def write_cities(self, cities):
        for tier in basemap.TIERS:
            payload = {"tier": tier, "ppm": basemap.PPM[tier], "q": self.Q,
                       "tolerance_deg": 0.0, "layers": {}, "cities": cities}
            with gzip.open(basemap.path(tier), "wt", encoding="utf-8") as fh:
                json.dump(payload, fh)

    def test_a_name_sits_on_its_towns_pin(self):
        self.write_cities([["Nampa", "ID", 200000], ["Salt Lake City", "UT", 1000000]])
        got = {c[0]: c for c in basemap.layers(home(), "coarse")["cities"]}
        pins = {p["place"]: p for p in mapview.points(
            [{"job_id": 1, "location": "Nampa, ID", "remote": "onsite"},
             {"job_id": 2, "location": "Salt Lake City, UT", "remote": "onsite"}], home())[0]}
        for name in ("Nampa", "Salt Lake City"):
            with self.subTest(name=name):
                self.assertAlmostEqual(got[name][1], pins[name]["x"], delta=0.001)
                self.assertAlmostEqual(got[name][2], pins[name]["y"], delta=0.001)

    def test_a_name_the_gazetteer_does_not_know_is_left_out(self):
        """Natural Earth spells Bartlett, TN "Barlett". Left out, not guessed."""
        self.write_cities([["Barlett", "TN", 60000], ["Nampa", "ID", 200000]])
        self.assertEqual([c[0] for c in basemap.layers(home(), "coarse")["cities"]], ["Nampa"])

    def test_names_outside_the_chunk_are_not_sent(self):
        self.write_cities([["Miami", "FL", 5000000], ["Nampa", "ID", 200000]])
        self.assertEqual([c[0] for c in basemap.layers(home(), "fine")["cities"]], ["Nampa"])

    def test_each_tier_keeps_bigger_places_than_the_next(self):
        tool = build_tool()
        raw = {k: [] for k in ("land", "county", "urban", "lake", "lake_fine", "road", "road_i")}
        raw["cities"] = [("Big", "CA", 2_000_000), ("Mid", "CA", 200_000), ("Small", "CA", 60_000)]
        names = {t: [c[0] for c in tool.build_basemap_tier(t, raw)["cities"]]
                 for t in tool.TIERS}
        self.assertEqual(names, {"coarse": ["Big"], "medium": ["Big", "Mid"],
                                 "fine": ["Big", "Mid", "Small"]})

    @unittest.skipUnless(basemap.available(), "no shipped basemap")
    def test_every_shipped_name_resolves_but_one(self):
        """St. Charles, MD has no place in the 2024 Gazetteer, so no point
        to stand on; every other name does (n26 aliased the two misspelt)."""
        with gzip.open(ROOT / "jsa" / "resources" / "data" / "us_basemap_fine.json.gz", "rt", encoding="utf-8") as fh:
            cities = json.load(fh)["cities"]
        missing = [f"{c[0]}, {c[1]}" for c in cities if places.resolve(c[0], c[1]) is None]
        self.assertLessEqual(set(missing), {"St. Charles, MD"})

    def test_the_aliases_name_real_towns(self):
        tool = build_tool()
        for (_, state), name in tool.CITY_ALIASES.items():
            with self.subTest(name=name):
                self.assertIsNotNone(places.resolve(name, state))


class TestFineLakes(unittest.TestCase):
    """Up close, the same lakes from the Census's own outlines."""

    def test_the_fine_tier_draws_lakes_from_their_own_source(self):
        tool = build_tool()
        ring = [(-116.2, 43.6), (-116.1, 43.6), (-116.1, 43.7), (-116.2, 43.6)]
        raw = {k: [] for k in ("land", "county", "urban", "road", "road_i")}
        raw["lake"], raw["lake_fine"], raw["cities"] = [], [ring], []
        self.assertEqual(len(tool.build_basemap_tier("fine", raw)["layers"]["lake"]), 1)
        self.assertEqual(tool.build_basemap_tier("medium", raw)["layers"]["lake"], [])

    def test_touching_is_shared_ground(self):
        tool = build_tool()
        square = [[(0, 0), (2, 0), (2, 2), (0, 2), (0, 0)]]
        inside = [[(0.5, 0.5), (1, 0.5), (1, 1), (0.5, 0.5)]]
        apart = [[(5, 5), (6, 5), (6, 6), (5, 5)]]
        self.assertTrue(tool._touches(square, inside))
        self.assertFalse(tool._touches(square, apart))

    def test_a_lake_is_filled_not_outlined(self):
        """The Census splits a lake at county lines; an outline would draw
        each seam across the water."""
        from tests.web_source import web_source
        src = web_source()
        self.assertNotIn("paint.stroke(p.lake)", src)


class TestPaintingOffThePagesThread(unittest.TestCase):
    """n26: a whole tier takes 25-100 ms to paint. It happens in a worker,
    from the page's own painting routine, with the page as the fallback."""

    SRC = web_source()

    def test_one_routine_for_page_and_worker(self):
        self.assertIn("paintGround.toString() + '\\n' + PAINTER", self.SRC)
        self.assertIn("paintGround(paint, ch.paths, msg)", self.SRC)
        self.assertIn('paintGround(g, p, m);', self.SRC)

    def test_a_failed_worker_falls_back_to_the_page(self):
        self.assertIn("painter.onerror = function () { bmLocal(); };", self.SRC)
        self.assertIn("if (!painter) { paint = canvas.getContext('2d'); }", self.SRC)

    def test_only_the_newest_picture_is_put_up(self):
        self.assertIn("if (!job || m.seq !== job.seq)", self.SRC)

    def test_the_frame_log_is_off_unless_asked_for(self):
        self.assertIn("get('debug') === 'frames'", self.SRC)


class TestTheGroundReadsInBothThemes(unittest.TestCase):
    """n26: water against land was 1.12:1 in dark mode -- a lake barely
    showed. At least 1.3:1 in both themes, and the ground stays quieter than
    the pins (roads, its loudest line, under 3:1)."""

    @staticmethod
    def contrast(a, b):
        def lum(h):
            c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
            return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
        hi, lo = sorted((lum(a), lum(b)), reverse=True)
        return (hi + 0.05) / (lo + 0.05)

    def themes(self):
        light, dark = re.findall(r"--map-water:(#\w{6});--map-land:(#\w{6});--map-urban:(#\w{6});"
                                 r"\s*--map-road:(#\w{6});--map-edge:(#\w{6})", web.BASE)
        return {"light": light, "dark": dark}

    def test_water_stands_off_land(self):
        for name, (water, land, *_rest) in self.themes().items():
            with self.subTest(theme=name):
                self.assertGreaterEqual(self.contrast(water, land), 1.3)

    def test_the_ground_stays_quiet(self):
        for name, (_water, land, _urban, road, _edge) in self.themes().items():
            with self.subTest(theme=name):
                self.assertLess(self.contrast(road, land), 3.0)


class TestTheTurn(unittest.TestCase):
    """Zoomed out, the live map turns to the usual map of the US. North-up
    at home tipped the country over by as much as its meridians converge."""

    def test_no_turn_at_the_usual_maps_centre(self):
        self.assertAlmostEqual(mapview.albers_turn(places.Place("", "", 39.0, -96.0)), 0.0)

    def test_west_turns_clockwise_and_east_the_other_way(self):
        """On the usual map the Pacific Northwest leans down to the east
        (negative), New England up (positive), by n * the longitude gap."""
        west = mapview.albers_turn(place("Portland", "OR"))
        east = mapview.albers_turn(place("New York", "NY"))
        self.assertLess(west, 0)
        self.assertGreater(east, 0)
        n = 0.5 * (math.sin(math.radians(29.5)) + math.sin(math.radians(45.5)))
        self.assertAlmostEqual(west, n * math.radians(place("Portland", "OR").lon + 96), places=9)

    def test_it_is_the_albers_maps_own_lean(self):
        """Measured off `albers` itself: the direction of north at the
        origin on the usual map is turned by exactly this angle."""
        origin = place("Seattle", "WA")
        x0, y0 = mapview.albers(origin.lat, origin.lon)
        x1, y1 = mapview.albers(origin.lat + 0.01, origin.lon)
        lean = math.atan2(-(x1 - x0), y1 - y0)       # counter-clockwise from up
        self.assertAlmostEqual(lean, mapview.albers_turn(origin), delta=1e-4)

    def test_the_page_is_given_it(self):
        from tests.test_live_map import TestThePage as Live

        live = Live()
        live.setUp()
        self.addCleanup(live.doCleanups)
        data = live.data(live.page())
        self.assertAlmostEqual(data["turn"], mapview.albers_turn(home()), places=6)


class TestReproducible(unittest.TestCase):
    def test_the_same_tier_twice_is_the_same_bytes(self):
        tool = build_tool()
        ring = [(-116.2, 43.6), (-116.1, 43.6), (-116.1, 43.7), (-116.2, 43.6)]
        raw = {"land": [ring], "county": [ring], "urban": [ring], "lake": [ring],
               "lake_fine": [ring], "road": [ring[:2]], "road_i": [ring[:2]],
               "cities": [("Boise City", "ID", 700000)]}
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
