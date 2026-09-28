# ADR 0015 — A map that agrees with its filter

Status: accepted; decision 2 amended by ADR 0019
Date: 2026-09-27
Relates to: ADR 0002 (geographic scope), ADR 0014 (distance, not place names).

## Context

n17 placed every posting on a map. n18 put "within [N] miles of [here]" on the
Matches page and answered it in numbers: *within 25 miles of Los Angeles, CA:
23 near you, 37 remote*.

Numbers answer "how many". They do not answer **"does fifty miles reach
Example Town"**, which is the question somebody asks before they move the slider,
and which one picture answers.

## Decision

Draw the map, and make it impossible for the picture to disagree with the
filter.

### 1. Measure first, draw second

The local map is an **azimuthal equidistant projection centred on the
operator**. A posting's distance comes from the same `places.nearest` the
filter uses; its bearing sets the direction; distance scales linearly to
pixels. So a dot is inside the drawn circle **if and only if** the posting is
within the radius — by construction, not by luck, and `tests/test_map.py`
checks it at every radius the slider offers plus the exact boundary.

This is the whole reason the module exists. A circle that says a job is in
reach when the list says it is not is worse than no circle, because the
operator believes the picture. Any reprojection onto a conic or a web-mercator
tile grid breaks the guarantee, and the test fails when it does.

### 2. Two scales, two projections, and an outline drawn only where it is true

- **Local** (a radius is set): the circle, bubbles, town names, a scale bar,
  and **no coastline**. The shipped outline is a 1:20,000,000 generalisation.
  On a fifty-mile-wide map it draws a straight line across the mouth of a bay,
  and would show a real job in the sea. Omitting it is a rule, not an
  oversight. *(Amended 2026-09-28 by ADR 0019: the local map now has ground
  under it, from 1:500,000 data measured good to about 0.1 mile. The
  1:20,000,000 outline is still never drawn at this scale.)*
- **National** ("anywhere in the US"): Albers equal-area conic, the projection
  the lower 48 is the right shape in, with the outline drawn at the scale it
  was generalised for.

### 3. The outline ships, like the centroids did

Census cartographic boundaries, 1:20,000,000, public domain, the same source
family as the Gazetteer files. Measured:

| tolerance | points | gzipped |
|---|---|---|
| raw | 13,698 | 83 KB |
| **0.05°, 3 dp** | **2,717** | **14 KB** |
| 0.15°, 2 dp | 1,046 | 4 KB |

0.05° is 3.5 miles — under one pixel on a map of the whole country, which is
the only scale it is drawn at.

**No tiles.** A tile layer would send the operator's home location to a map
server, one request per tile, every time the page loads. ADR 0014 refused a
geocoder for exactly this reason and the reason has not changed: a home ZIP is
identity. `tests/test_map.py` walks the import graph of the drawing path and
fails if anything in it can reach the network.

### 4. Nothing is dropped in silence

A remote posting is not a dot: it has no distance, and drawing it at the
office it names would put a job you can do from bed on the far side of the
country. A posting that cannot be placed is not a dot either — unknown is not
far away, and a guess is the one mistake this module must not make. Anything
beyond the edge of the frame, and anything in Alaska, Hawaii or Puerto Rico on
the national map, is counted too. All four counts are printed under the map in
words, and the totals add up to the number of postings the page considered.

Alaska and Hawaii get no inset: measured 2026-09-27, **0 of 998** unreviewed
postings place in either, and an inset is a second scale on one picture.

### 5. The control is a slider and a toggle

n18's six-value dropdown is gone. A radius is continuous with an off switch,
so it is a slider in miles (5 to 300, clamped) plus *anywhere in the US* —
which is what the operator asked for, and one control per question.

It works with scripts off: both are form controls and *Filter* submits them,
and the SVG is in the HTML the server sends. With scripts on, the circle and
its label follow the handle and releasing it submits. **The script may resize
and relabel. It may not decide which postings match** — that answer is the
server's, and a browser quietly disagreeing with it is the bug this whole goal
exists to prevent.

### 6. A bare visit uses the profile's radius

Opening your own dashboard is not a request for the whole country: the profile
already says how far you would go. Any query string is obeyed literally, so
every link on the page — including n18's "Show them anyway" — keeps meaning
what it says.

## What this turned up

Rebuilding the shipped data from a written-down rule (`tools/build_map_data.py`)
exposed a bug nobody could see. The original build stripped the Census name's
legal descriptor by guessing at the trailing words, which mangled twenty rows:
`Nashville-Davidson metropolitan government` became `Nashville-Davidson` in
some rows and `Lexington-Fayette urban` in others, and `Carson City` lost the
word "city" twice and became `Carson`.

**Every posting in Nashville, Macon, Athens, Augusta, Lexington, Butte, Juneau
and Carson City was unplaceable**, and nothing reported it: an unplaced
posting is not an error, so six job markets were quietly invisible.

The rule is now the LSAD code the row declares, applied once. The loader also
indexes the name people actually say — the head of a consolidated city-county
name, and `St.`/`Saint` either way round — never producing a state name, since
"Oklahoma City" answering to "Oklahoma" would land a posting 1,100 miles from
where it thinks it is. Measured on a list of the hundred largest US cities:
**99/110 placed before, 110/110 after**, with no posting in the author's own
tracker changing where it sits.

## Rejected alternatives

- **Map tiles (OpenStreetMap, Mapbox, anything).** Better-looking, and they
  tell a stranger where the operator lives on every page load.
- **A national outline on the local map.** See above: at that scale the
  generalisation is a lie, and it is a lie about where the sea is.
- **Insets for Alaska and Hawaii.** Two scales on one picture, for zero
  postings. Counted in words instead.
- **Computing matches in the browser so the slider updates without a round
  trip.** Two implementations of one rule, and the wrong one on screen.
- **Drawing one dot per posting.** Sixty jobs in Los Angeles is one town;
  sixty dots on one pixel is a lie about how many places there are.
- **Labelling the largest towns near the operator.** The shipped Gazetteer has
  no population, so "largest" could only be guessed, and a map that names a
  hamlet while leaving a city blank is worse than one that names neither. It
  labels the towns with the most postings, which is what the map is about.

## Consequences

- `data/us_outline.json.gz` joins the shipped data: 15 KB, total now 700 KB.
- `data/us_places.csv.gz` changed in 20 rows. Anyone who has already run
  `jsa discover` should run `jsa rescore` to pick up the newly placeable
  cities.
- `tools/build_map_data.py` is the only file in the project allowed to open a
  socket for reference data, and nothing in `jsa/` imports it. A test asserts
  both halves of that.
- The Matches page now renders an SVG per request, which parses every
  unreviewed posting's location. Measured over 998 postings: **7 ms** for the
  local map, **10 ms** for the national one. The second pass over
  `v_new_matches` that feeds it costs **77 ms**, which is the real price and
  is the view's, not the map's; the page went from about 190 ms to 280 ms on
  the author's tracker. If that ever matters, the fix is the view, not the
  drawing.
