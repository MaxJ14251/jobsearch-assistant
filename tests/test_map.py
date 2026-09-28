"""The map on the Matches page.

One property matters more than everything else here: the picture cannot
disagree with the filter it is a picture of. A drawn circle that says a job
is in reach when the list says it is not is worse than no circle at all,
because the operator believes the picture. TestTheCircleAgrees is the test
that would fail if that ever stopped being true.

The rest is about what a map must never do quietly: guess a position for a
posting it cannot place, drop one off the edge without saying so, or give a
remote job a distance it does not have.
"""

import ast
import math
import shutil
import tempfile
import unittest
from pathlib import Path

from jsa import db, mapview, places
from jsa.config import ROOT

PROFILE = {
    "identity": {"full_name": "Dana Rivers"},
    "job_search_preferences": {
        "target_titles": ["Engineer"],
        "locations": ["Remote (US)", "Boise, ID"],
        "compensation_floor_usd": "no_floor",
    },
}

# Distances from Boise, roughly: Meridian 10, Nampa 18, Twin Falls 110,
# Salt Lake City 290, Miami 2,100.
SPREAD = [
    {"job_id": 1, "title": "Near", "location": "Meridian, ID", "remote": "onsite"},
    {"job_id": 2, "title": "Nearish", "location": "Nampa, ID", "remote": "onsite"},
    {"job_id": 3, "title": "County", "location": "Twin Falls, ID", "remote": "onsite"},
    {"job_id": 4, "title": "State", "location": "Salt Lake City, UT", "remote": "onsite"},
    {"job_id": 5, "title": "Far", "location": "Miami, FL", "remote": "onsite"},
    {"job_id": 6, "title": "Anywhere", "location": "Redmond, WA", "remote": "remote"},
    {"job_id": 7, "title": "Mystery", "location": "Starbase, TX", "remote": "onsite"},
]


def home():
    return places.resolve("Boise", "ID")


class TestTheCircleAgrees(unittest.TestCase):
    """A dot is inside the drawn circle exactly when the posting is in range.

    Not approximately: the projection places a posting by its measured
    distance, so the same number decides both. If somebody ever reprojects
    this map onto a conic or a web-mercator tile grid, these fail.
    """

    def check(self, radius):
        view = mapview.local(SPREAD, home(), radius)
        centre = (view.width / 2, view.height / 2)
        for bubble in view.bubbles:
            drawn = math.hypot(bubble.x - centre[0], bubble.y - centre[1])
            with self.subTest(place=bubble.place, radius=radius):
                # what the picture says
                looks_inside = drawn <= view.circle + 1e-6
                # what the filter says, from the same source as jsa.web
                really_inside = places.nearest(
                    home(), [r for r in SPREAD
                             if r["job_id"] == bubble.job_id][0]["location"]
                ) <= radius
                self.assertEqual(looks_inside, really_inside, bubble.place)
                self.assertEqual(bubble.inside, really_inside)

    def test_at_every_radius_the_slider_offers(self):
        for radius in (5, 10, 25, 40, 50, 100, 150, 250, 300):
            self.check(radius)

    def test_a_posting_sitting_exactly_on_the_radius_is_inside(self):
        """The boundary case, because <= and < differ by one job."""
        exactly = places.miles(home(), places.resolve("Twin Falls", "ID"))
        view = mapview.local(SPREAD, home(), exactly)
        twin = next(b for b in view.bubbles if b.place == "Twin Falls")
        self.assertTrue(twin.inside)
        drawn = math.hypot(twin.x - view.width / 2, twin.y - view.height / 2)
        self.assertLessEqual(drawn, view.circle + 1e-6)

    def test_the_page_and_the_map_hide_the_same_postings(self):
        """End to end: what the list drops is what the map draws faintly,
        is remote, or could not be placed. Nothing falls between them."""
        from jsa.web import _by_distance

        rows = [dict(r) for r in SPREAD]
        view = mapview.local(SPREAD, home(), 25)
        kept, hidden, unplaced = _by_distance(rows, home(), 25.0)

        drawn_inside = sum(b.count for b in view.bubbles if b.inside)
        self.assertEqual(len(kept), drawn_inside + view.remote)
        self.assertEqual(hidden, len(SPREAD) - len(kept))
        self.assertEqual(unplaced, view.unplaced)


class TestGeometry(unittest.TestCase):
    def test_north_is_up(self):
        """Seattle is north of Portland; on the map it is above it."""
        portland = places.resolve("Portland", "OR")
        view = mapview.local(
            [{"job_id": 1, "title": "n", "location": "Seattle, WA", "remote": "onsite"}],
            portland, 250)
        self.assertLess(view.bubbles[0].y, view.height / 2)

    def test_east_is_right(self):
        boise = home()
        view = mapview.local(
            [{"job_id": 1, "title": "e", "location": "Denver, CO", "remote": "onsite"}],
            boise, 1000)
        self.assertGreater(view.bubbles[0].x, view.width / 2)

    def test_a_known_distance_lands_at_the_right_radius(self):
        """Twin Falls is 110 miles away, so on a 100-mile map it sits at
        about 110/155ths of the way to the edge of the usable half."""
        view = mapview.local(SPREAD, home(), 100)
        twin = next(b for b in view.bubbles if b.place == "Twin Falls")
        drawn = math.hypot(twin.x - view.width / 2, twin.y - view.height / 2)
        true_miles = places.miles(home(), places.resolve("Twin Falls", "ID"))
        self.assertAlmostEqual(drawn / view.circle, true_miles / 100, places=4)

    def test_the_scale_bar_is_the_length_it_claims(self):
        view = mapview.local(SPREAD, home(), 50)
        self.assertAlmostEqual(view.scale_px / view.scale_miles,
                               view.circle / 50, places=6)

    def test_the_national_scale_bar_is_honest_to_a_few_percent(self):
        """Albers is equal-area, not equidistant, so the bar is the scale in
        the middle of the map. Check it against a coast-to-coast pair."""
        view = mapview.national([
            {"job_id": 1, "title": "w", "location": "San Francisco, CA",
             "remote": "onsite"},
            {"job_id": 2, "title": "e", "location": "New York, NY",
             "remote": "onsite"}])
        west = next(b for b in view.bubbles if b.place == "San Francisco")
        east = next(b for b in view.bubbles if b.place == "New York")
        drawn = math.hypot(east.x - west.x, east.y - west.y)
        true_miles = places.miles(places.resolve("San Francisco", "CA"),
                                  places.resolve("New York", "NY"))
        claimed = drawn / view.scale_px * view.scale_miles
        self.assertLess(abs(claimed - true_miles) / true_miles, 0.05)

    def test_a_bubble_counts_every_posting_in_its_town(self):
        rows = [dict(SPREAD[0], job_id=n) for n in range(1, 6)]
        view = mapview.local(rows, home(), 25)
        self.assertEqual(len(view.bubbles), 1)
        self.assertEqual(view.bubbles[0].count, 5)
        self.assertEqual(view.shown, 5)


class TestWhatItRefusesToDraw(unittest.TestCase):
    def test_a_remote_posting_is_never_a_dot(self):
        """It has no distance. Drawing it at the office it names would put a
        job you can do from bed on the far side of the country."""
        view = mapview.local(SPREAD, home(), 300)
        self.assertNotIn("Redmond", [b.place for b in view.bubbles])
        self.assertEqual(view.remote, 1)

    def test_an_unplaceable_posting_is_never_a_dot_either(self):
        """Starbase, TX incorporated in 2025 and is not in the 2024 Census
        file. Unknown is not the same as far away, and neither is a guess."""
        view = mapview.local(SPREAD, home(), 300)
        self.assertEqual(view.unplaced, 1)
        self.assertNotIn("Starbase", [b.place for b in view.bubbles])

    def test_everything_off_the_edge_is_counted(self):
        view = mapview.local(SPREAD, home(), 25)
        drawn = sum(b.count for b in view.bubbles)
        self.assertEqual(drawn + view.off_map + view.remote + view.unplaced,
                         len(SPREAD))

    def test_alaska_and_hawaii_are_counted_not_dragged_to_the_edge(self):
        rows = [{"job_id": 1, "title": "a", "location": "Juneau, AK",
                 "remote": "onsite"},
                {"job_id": 2, "title": "b", "location": "Austin, TX",
                 "remote": "onsite"}]
        view = mapview.national(rows)
        self.assertEqual(view.off_map, 1)
        self.assertEqual([b.place for b in view.bubbles], ["Austin"])

    def test_the_national_map_only_labels_places_it_drew(self):
        view = mapview.national(SPREAD)
        drawn = {b.place for b in view.bubbles}
        self.assertTrue({label.text for label in view.labels} <= drawn)


class TestDegenerate(unittest.TestCase):
    """None of these may raise. A dashboard that 500s is worse than a plain
    one, and every case here is somebody's first day with the tool."""

    def test_no_postings_at_all(self):
        view = mapview.local([], home(), 25)
        self.assertEqual(view.bubbles, [])
        self.assertEqual(view.shown, 0)

    def test_every_posting_remote(self):
        rows = [dict(r, remote="remote") for r in SPREAD]
        view = mapview.local(rows, home(), 25)
        self.assertEqual(view.bubbles, [])
        self.assertEqual(view.remote, len(SPREAD))

    def test_every_posting_unplaceable(self):
        rows = [{"job_id": 1, "title": "x", "location": "Atlantis, ZZ",
                 "remote": "onsite"}]
        view = mapview.local(rows, home(), 25)
        self.assertEqual(view.unplaced, 1)

    def test_a_posting_in_your_own_town(self):
        rows = [{"job_id": 1, "title": "x", "location": "Boise, ID",
                 "remote": "onsite"}]
        view = mapview.local(rows, home(), 25)
        self.assertEqual(view.bubbles[0].miles, 0)
        self.assertAlmostEqual(view.bubbles[0].x, view.width / 2, places=6)

    def test_no_origin_falls_back_to_the_national_map(self):
        view = mapview.build(SPREAD, None, 25)
        self.assertEqual(view.kind, "national")

    def test_no_radius_is_the_national_map(self):
        self.assertEqual(mapview.build(SPREAD, home(), None).kind, "national")

    def test_a_missing_outline_file_draws_no_map_rather_than_a_bad_one(self):
        original = mapview.OUTLINE_FILE
        mapview.outline.cache_clear()
        mapview.OUTLINE_FILE = Path("does-not-exist.json.gz")
        try:
            view = mapview.national(SPREAD)
            self.assertEqual(view.kind, "none")
            self.assertFalse(view.drawn)
        finally:
            mapview.OUTLINE_FILE = original
            mapview.outline.cache_clear()


class TestNothingHereReachesTheNetwork(unittest.TestCase):
    """A tile server would learn where the operator lives, one request per
    tile, every time the page loads. ADR 0014 refused a geocoder for the same
    reason. Proven on the import graph rather than promised in a docstring."""

    FORBIDDEN = {"httpx", "requests", "urllib", "socket", "http", "ftplib",
                 "smtplib"}

    def imports_of(self, module: str) -> set[str]:
        tree = ast.parse((ROOT / "jsa" / f"{module}.py").read_text("utf-8"))
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    found.update(a.name for a in node.names)
                if node.module:
                    found.add(node.module.split(".")[0])
        return found

    def test_the_drawing_path(self):
        seen, stack = set(), ["mapview"]
        while stack:
            module = stack.pop()
            if module in seen or not (ROOT / "jsa" / f"{module}.py").exists():
                continue
            seen.add(module)
            names = self.imports_of(module)
            self.assertFalse(names & self.FORBIDDEN,
                             f"jsa/{module}.py can reach the network")
            stack.extend(names)
        self.assertIn("places", seen, "the walk never got past the first file")

    def test_only_the_build_tool_may_fetch(self):
        """tools/build_map_data.py downloads the Census sources. It is the
        one exception, and it is not importable from jsa/."""
        source = (ROOT / "tools" / "build_map_data.py").read_text("utf-8")
        self.assertIn("urllib.request", source)
        # Imports, not mentions: doctor names the tool so a reader can run it.
        for path in sorted((ROOT / "jsa").glob("*.py")):
            names = self.imports_of(path.stem) | {
                n.module or "" for n in ast.walk(ast.parse(path.read_text("utf-8")))
                if isinstance(n, ast.ImportFrom)}
            self.assertFalse({"tools", "build_map_data"} & names
                             or any("build_map_data" in n for n in names),
                             f"{path.name} imports the build tool")


class TestTheBuildRule(unittest.TestCase):
    """The shipped data is only checkable if the rule that made it is. These
    are the rows that were wrong before the rule was written down."""

    def setUp(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "build_map_data", ROOT / "tools" / "build_map_data.py")
        self.build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.build)

    def test_the_descriptor_the_row_declares_is_the_one_stripped(self):
        cases = [
            ("Abbeville city", "25", "Abbeville"),
            ("Abanda CDP", "57", "Abanda"),
            ("Juneau city and borough", "53", "Juneau"),
            ("Copperton metro township", "35", "Copperton"),
            ("Lexington-Fayette urban county", "UC", "Lexington-Fayette"),
            ("Boise City city", "25", "Boise City"),
            # LSAD 00 means no descriptor -- unless "(balance)" says the row
            # is the remainder of a consolidated government, which spells it
            # out in the name.
            ("Carson City", "00", "Carson City"),
            ("Macon-Bibb County", "00", "Macon-Bibb County"),
            ("Milford city (balance)", "00", "Milford"),
            ("Nashville-Davidson metropolitan government (balance)", "00",
             "Nashville-Davidson"),
            ("Athens-Clarke County unified government (balance)", "00",
             "Athens-Clarke County"),
        ]
        for raw, lsad, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(self.build.plain_name(raw, lsad), expected)

    def test_simplify_keeps_the_ends_and_the_corners(self):
        square = [(0, 0), (1, 0.001), (2, 0), (2, 2), (0, 2), (0, 0)]
        thinned = self.build.simplify(square, 0.05)
        self.assertEqual(thinned[0], (0, 0))
        self.assertEqual(thinned[-1], (0, 0))
        self.assertNotIn((1, 0.001), thinned)   # all but straight
        self.assertIn((2, 2), thinned)          # a corner

    def test_the_outline_covers_the_lower_48(self):
        drawn = set(mapview.outline()) - set(mapview.OFFSHORE)
        self.assertEqual(len(drawn), 49, "48 states and DC")


class TestOnThePage(unittest.TestCase):
    """The SVG is in the HTML the server sends: no script has to run for the
    map to be there, and none runs to decide what it says."""

    def setUp(self):
        from tests.test_web_documents import make_client

        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        path = self.dir / "t.db"
        db.init_db(path)
        con = db.connect(path)
        for row in SPREAD:
            con.execute("INSERT INTO companies (id,name,slug) VALUES (?,?,?)",
                        (row["job_id"], f"Co {row['job_id']}",
                         f"co-{row['job_id']}"))
            con.execute(
                "INSERT INTO jobs (id,company_id,title,url,location,remote,"
                "match_score,track,description) VALUES (?,?,?,?,?,?,0.8,"
                "'engineering','Python work.')",
                (row["job_id"], row["job_id"], row["title"],
                 f"https://acme.test/{row['job_id']}", row["location"],
                 row["remote"]))
        con.commit()
        con.close()
        self.client, _ = make_client(path, self.dir, profile=PROFILE)

    def page(self, query=""):
        response = self.client.get("/" + query)
        self.assertEqual(response.status_code, 200)
        return response.text

    def test_the_circle_is_in_the_html(self):
        self.assertIn('id="ring"', self.page("?radius=25"))

    def test_the_national_map_is_in_the_html(self):
        text = self.page("?anywhere=1")
        self.assertIn('class="land"', text)
        self.assertNotIn('id="ring"', text)

    def test_the_caption_says_what_is_not_drawn(self):
        text = self.page("?radius=25")
        self.assertIn("remote, with no distance to draw", text)
        self.assertIn("naming a place this could not find", text)

    def test_a_bare_visit_uses_the_radius_in_the_profile(self):
        """Somebody opening their own dashboard is not asking for the whole
        country; their profile already says how far they would go."""
        self.assertIn("within 40 miles of Boise", self.page())

    def test_a_query_string_is_obeyed_literally(self):
        """So that every link on the page keeps meaning what it says --
        including n18's "Show them anyway"."""
        self.assertNotIn("within", self.page("?anywhere=1").split("<form")[0])

    def test_the_slider_is_a_form_control(self):
        text = self.page("?radius=25")
        self.assertIn('type="range"', text)
        self.assertIn('name="radius"', text)
        self.assertIn('name="anywhere"', text)

    def test_an_out_of_range_radius_is_clamped_not_refused(self):
        self.assertIn("within 300 miles", self.page("?radius=99999"))
        self.assertIn("within 5 miles", self.page("?radius=1"))

    def test_a_dot_links_to_the_job(self):
        self.assertIn('<a href="/job/1"><circle', self.page("?radius=25"))


if __name__ == "__main__":
    unittest.main()
