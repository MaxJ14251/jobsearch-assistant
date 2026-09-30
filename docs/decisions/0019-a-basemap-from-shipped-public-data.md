# ADR 0019 — A basemap from shipped public data

Status: accepted
Date: 2026-09-28
Amends: ADR 0015 decision 2 (no coastline on the local map), ADR 0018 (the
live map). Relates to: ADR 0014 (distance, not place names).

## Context

The live map (ADR 0018) sat on a flat backdrop: range rings, the tinted
radius fence and the pins, but no land, water, road or town under them. A
pin 23 miles away was placed correctly, but nobody could tell whether it
was across the bay or over the mountains.

Two earlier decisions ruled out the obvious fixes:

- **No tiles.** A tile server learns where the operator lives, one request
  per tile, every time the page loads (ADR 0014, 0015). That has not changed.
- **No coastline on the local map.** The shipped outline is 1:20,000,000.
  At fifty miles across it would draw a straight line where a bay is and put
  a real job in the sea (ADR 0015 decision 2).

What was needed was data good enough to lift the second refusal without
breaking the first.

## Decision

### 1. The ground ships with the tool, and all of it is public domain

`tools/build_map_data.py` (the one file allowed to fetch reference data)
downloads the sources once (about 350 MB, cached), simplifies them, and
writes three files to `data/`. The page never makes a request to any other host.

| Layer | Source | Licence | Raw | Used for |
|---|---|---|---|---|
| Land and coast | Census cartographic boundaries, states, 2023, 1:500,000 | public domain (17 USC 105) | 56 shapes, 285,640 points | Land is the union of the states. These files are clipped to the shoreline, Great Lakes included. |
| County lines | Census cartographic boundaries, counties, 2023, 1:500,000 | public domain | 3,235 shapes, 1,033,837 points | Fine tier only |
| Built-up areas | Census 2020 Urban Areas (corrected), 1:500,000 | public domain | 2,644 shapes, 855,553 points | |
| Roads | TIGER/Line 2024 primary roads (S1100: interstates, US and state freeways) | public domain | 17,522 lines, 3,584,607 points | Coarse tier: interstates only |
| Inland lakes | Natural Earth 10m lakes and North America supplement | public domain (Natural Earth's own dedication) | 1,689 shapes in the US area, 124,286 points | Coarse and medium tiers; which lakes exist, at every tier (see 3) |
| Lake outlines, fine tier | TIGER/Line 2024 area water, only for the 593 counties a Natural Earth lake touches | public domain | 593 files, about 250 MB to download | The same lakes, drawn from the Census's own shoreline (see 3) |
| City names | Natural Earth 10m populated places | public domain | 420 US places of 50,000 or more | Name, state and population only; the position is the Gazetteer's (see 4c) |
| Town points | Census 2023 places, 1:500,000 | public domain | | Only to correct six town points (see "What this turned up") |

OpenStreetMap was not used. Its ODbL licence would place attribution and
share-alike obligations on a database shipped inside a public repository,
and that is a decision for the owner. Nothing here needed it.

### 2. "Lines up" has two parts, and they are kept apart

**Projection alignment is exact, by construction.** `mapview.projector(origin)`
is the one function that turns latitude and longitude into map miles. It
places every pin (`points`, the static map) and every basemap vertex
(`jsa.basemap`). The formula is never written a second time, and never in
JavaScript. The page draws the ground with the same view numbers (scale and
offset) it draws the pins with. Tests pin this down:

- The pins and the ground call the same function (a spy, not a comparison
  of outputs).
- A basemap vertex placed exactly on a town's coordinates lands on that
  town's pin within 0.001 mile, for origins in three states, with the town
  on both sides of the origin.
- The projector's hypot is the great-circle distance to within 1e-6 mile,
  and its angle is `bearing()`.

**Data alignment is as good as the data, and it is measured.** 4,000
vertices of the full-resolution TIGER coastline were compared with the
1:500,000 shore (distance in miles from each TIGER vertex to the nearest
500k edge):

| Sample | median | p90 | p95 | p99 | max |
|---|---|---|---|---|---|
| All TIGER coastline, lower 48 | 0.018 | 0.141 | 0.460 | 2.64 | 16.8 |
| Mainland lines only (islands excluded) | 0.015 | 0.072 | 0.134 | 0.77 | 12.6 |
| Mainland lines, Louisiana delta excluded | 0.014 | 0.061 | 0.105 | 0.50 | 5.2 |
| Pacific coast | 0.016 | 0.052 | 0.073 | 0.17 | 1.9 |

The long tail is missing features, not misplaced ones. Of the 451 TIGER
coastline features with a vertex more than a mile from the 500k shore, 419
are closed rings: small islands and marsh islands, mostly in the
Mississippi delta, that the 1:500,000 file leaves out. Where the mainland
coast is drawn, it is within about a tenth of a mile.

### 3. Three tiers, each honest at its own zoom; past the finest, the ground fades

Each tier is simplified (Ramer–Douglas–Peucker) to half a pixel at the
largest scale it is drawn at. Shapes under 2 px across at that scale are
left out, since a speck says nothing.

| Tier | Drawn up to | Tolerance | Coordinates | Layers | Points | File |
|---|---|---|---|---|---|---|
| coarse | 0.8 px/mi | 0.625 mi | 1/500° | land, lakes, built-up, interstates | 114,622 | 338 KB |
| medium | 4 px/mi | 0.125 mi | 1/2000° | land, lakes, built-up, primary roads | 521,349 | 1,417 KB |
| fine | 20 px/mi | 0.025 mi | 1/10000° | land, counties, built-up, lakes (Census), primary roads | 2,372,205 | 6,065 KB |

The page uses the coarsest tier that is still honest at the current scale.
While that tier loads it may show a finer one, never a coarser one, whose
simplified coast would visibly be in the wrong place.

**The zoom limit is 20 px/mi**, because the coast's measured p95 error of
about 0.1 mile is 2 px at that scale. The live map can zoom much further
(to 250 px/mi) so that pins in neighbouring towns separate. Capping the
zoom at 20 would have stopped them separating. So instead, past 20 px/mi
the ground fades out, to nothing by 40 px/mi, and the map says **"Map
detail ends at this zoom"**. A basemap that is subtly wrong close up would
look authoritative, which is worse than none.

Natural Earth's lakes are 1:10,000,000, honest only up to the medium tier.
The first version therefore had no lakes at the fine tier, and a lake
vanished as you zoomed in. *Fixed the same day:* at the fine tier the SAME
lakes are drawn from the Census's own water areas. Those come county by
county, 3,235 files and 1.1 GB for the country, so only the 593 counties a
Natural Earth lake touches are fetched. A Census piece is kept when:

1. it is a lake or reservoir of at least a quarter of a square mile whose
   interior lies within half a mile of a Natural Earth lake (Natural
   Earth's shore is about that far off); or
2. it has the same name as a kept piece and lies within a mile of it,
   repeated until nothing more joins. The Census splits the Great Salt
   Lake into more than 20 pieces. Some lie miles outside Natural Earth's
   older, high-water outline, and some are filed as stream or canal. On
   the first pass the lake came back cut off at a county line. A name alone
   is not enough ("Mud Lk" is a hundred lakes), hence the mile.

Lakes are filled, not outlined, because the Census splits a lake at every
county line, and an outline would draw each seam across the water. Only
lakes inside the land are kept at any tier: the Great Lakes are already
the edge of the land, and two shorelines a mile apart would be a lie.

### 4. Served in pieces from this machine, painted once per settled view

- **A same-origin route**, `GET /basemap/{tier}?home=&x=&y=`, sits behind
  the same Host check as every other route. It returns one SVG path per
  layer, in map miles, cut to a box around the view: fine is cut to 300
  miles, medium to 1,300, and coarse is the whole country. The page's
  radius slider re-fetches the whole page when it settles, so putting the
  ground in the page itself would have re-sent megabytes on every settle.
  Results are cached per origin, tier and snapped centre. The browser may
  keep a chunk for a day, and the URL carries a version of the data files
  (`v`), so a rebuild is never hidden behind that cache. The server loads
  all three tiers in the background when it starts: about 3 s, then 42 MB
  held, as 4-byte arrays; as Python lists the fine tier alone was 87 MB.
- **A canvas behind the svg**, not paths inside it. The first version
  appended the paths to the svg's `world` group, and a pan then repainted
  megabytes of outline on every frame. Now the ground is painted once into
  a canvas, with the pins' transform shifted by a margin. While the view
  moves, the canvas is only moved and scaled with a CSS transform, the same
  affine change the pins go through. It is repainted when the view settles.
- **Painted in a worker** (n26). Repainting a whole tier takes 25 to 100
  ms, and on the page's own thread that was a hitch each time the map
  stopped. A web worker paints on an OffscreenCanvas and hands back the
  finished picture. The page puts it up with `transferFromImageBitmap`,
  which takes about 0.1 ms, and until then keeps showing the old picture,
  moved with the view. Only the newest request is painted; a picture that
  went stale in flight is discarded. The worker runs the page's own
  `paintGround()`, from its source, so the two cannot draw differently. A
  browser without OffscreenCanvas, or a worker that fails, falls back to
  painting on the page as before.
- The canvas is `aria-hidden`, takes no pointer events, and adds no tab
  stop. Its colours are theme tokens (`--map-water`, `--map-land`,
  `--map-urban`, `--map-road`, `--map-edge`), redefined for dark mode.
  Water against land is at least 1.3:1 in both themes (n26: 1.38 light and
  1.33 dark, from 1.23 and 1.12; a test holds it), and roads stay under
  3:1 so the pins remain the loudest thing on the map. The canvas
  repaints when the colour scheme changes. The radius fence, commute
  bands and status colours stay the loudest things on the map.
- **The no-JavaScript map** (`mapview.local`) draws the same layers from the
  same data through the same projector, cut to its own frame. It has no
  ground past 20 px/mi.

### 4b. Zoomed out, the map turns to the usual map of the US

*Added the same day, after the owner said the states looked "cockeyed".*

The map has north straight up at the operator. Everywhere else, north
leans by as much as the meridians converge. On the usual map of the United
States (Albers, centred on 96° W) the middle of the country is level. From
a home far from 96° W, the north-up map showed the whole country tipped
over: about 16° from Portland, Oregon.

The live map now turns about home by the Albers map's own lean at home,
`mapview.albers_turn` = n × (longitude + 96°). A test measures that lean
off `albers` itself.

- **The turn fades with zoom:** the full angle below 0.6 px/mi, none above
  3 px/mi, and a smooth blend between. Local maps, where streets and pins
  are read, stay north-up.
- **Nothing is re-projected.** A turn about home moves no distance, so the
  radius circle, the rings and every `d` are exactly what they were.
- **One matrix does it.** The pins, the rings and the ground all go through
  one view matrix (`frame()` in the page), so they turn together. The
  canvas's in-motion transform includes the turn.
- **A north arrow** appears whenever the map is turned.

### 4c. City names, on the pins' own points

ADR 0015 refused labelling the largest towns because the Gazetteer has no
population. Natural Earth's populated places do, and are public domain.
Only the name, state and metro population ship: 93 places at the coarse
tier (500,000 or more), 213 at medium (150,000 or more), and 420 at fine
(50,000 or more). The server puts each name on the Gazetteer point of the
same town, the point any pin for that town uses, so a city's name and its
pins cannot disagree.

Two Natural Earth names are misspelt and are aliased in the build tool
(`CITY_ALIASES`, n26): "Barlett, TN" is Bartlett and "Wilkes Barre, PA" is
Wilkes-Barre. One, "St. Charles, MD", has no place in the 2024 Gazetteer
at all. It is left off rather than put on a neighbour's point, so 419 of
420 names are drawn. The page draws names after the
jobs' own labels, so a job's label always wins the space. No name sits on
a marker or on home, bigger places come first, at most 40 names are drawn
at once, and none are drawn once the ground has faded out.

### 5. Missing data is a flat map, not an error

Without the files, the route answers 404 and the page draws exactly what it
drew before n23. `jsa doctor` reports it as advisory and gives the command
that rebuilds the files.

### 6. The basemap decides nothing

Inside or outside the radius is still `d` from `places.nearest`. The
basemap cannot move, hide or count a pin, and a test checks that the points
are identical with it and without it.

## What this turned up

- **San Francisco's town point was about 30 miles out to sea.** A Census
  internal point is inside the town's area *including its water*, and San
  Francisco's area includes the Farallon Islands. Every San Francisco
  posting was measured from the ocean. Measured against the 1:500,000 place
  boundaries, 70 of 32,333 town points fall outside their own town, and 6
  by more than half a mile: Egegik AK, San Francisco CA, Portland ME, Stacy
  MN, Vinita OK and Aransas Pass TX. The build now moves those six onto
  their town's largest piece of land (`OFFSHORE_MILES`). The other 64 are
  within the boundary file's own error and stay where Census put them, so a
  rebuild does not move towns for nothing. This corrects ADR 0014's data.
- **Towns with postings in the sea.** Of the 92 distinct places the
  tracker's postings name, 4 fell outside the 1:500,000 land before the
  fix. After it, 3 remain: coastal towns whose Census point sits on the
  beach, 0.00 to 0.05 mile outside the simplified shore. That is inside the
  shore's own measured error, and at most 1 px at the finest honest zoom.
  Across all 32,109 shipped town points, 23 fall outside the land.
- **The Docker image had no map data at all.** The Dockerfile never copied
  `data/`, so the containerised dashboard could place nothing on any map.
  It copies `data/*.gz` now, and `tools/docker_check.sh` fails if the town
  or basemap data is missing from the image.

## Measured

- **Build:** about 35 s from a download cache, 3 min with the county water
  files. A second build is byte-identical (the SHA-256 of all four files
  matched).
- **Size of `data/`:** 699 KB before, 7.5 MB after (all compressed), and
  8.3 MB with the fine-tier lakes and city names. That is just past the
  8 MB the goal set for asking the owner first; it was reported to them.
- **One chunk, for three centres** (none of them the operator's):

| Centre | coarse | medium | fine | cold build (medium / fine) |
|---|---|---|---|---|
| Seattle, WA | 292 KB | 1,363 KB | 296 KB | 509 / 100 ms |
| New York, NY | 291 KB | 1,240 KB | 1,187 KB | 461 / 367 ms |
| Chicago, IL | 290 KB | 1,449 KB | 1,018 KB | 541 / 315 ms |

  Sizes are gzip sizes of the route's JSON; the route itself sends it
  uncompressed, over loopback. Coarse takes about 130 ms to build cold, and
  a cached chunk under 10 µs. Nothing is on the page's critical path: the
  list and the pins render first, and the ground follows.
- **Network:** a full Matches load and a zoom session made requests to the
  loopback origin only.
- **Seen in the browser,** 10 views in all, none of them around the
  operator's home:
  - Seattle: a 300-mile radius and a 40-mile radius, zoomed in past the limit.
  - New York: the whole country, a 50-mile radius, and the finest zoom
    (20.5 px/mi).
  - Chicago: a 300-mile radius, a 50-mile radius and 12.8 px/mi, plus a
    50-mile radius in dark mode.

  In each, the coast, the lakes and the interstates sat where they should
  relative to the pins of towns on them: Seattle on Puget Sound, Milwaukee
  and Chicago on Lake Michigan, New York on its harbour with Long Island
  beside it. The first two Seattle views predate the canvas; the rest are
  of the version shipped.
- **Frame cost (n26, 2026-09-29), with `?debug=frames`.** Frame *gaps*
  cannot be measured in the in-app browser: it throttles a pane it
  considers hidden, and one gap came out at 1,006 ms with the page doing
  1 ms of work. So what was measured is the work the page itself does.
  These are New York (the heaviest chunks) at 1,200 x 850, with the paint
  timed until the pixels exist (`getImageData` forces that):

  | | coarse | medium | fine |
  |---|---|---|---|
  | page work per frame while panning or zooming | 1.4-1.5 ms | 1.3-2.1 ms | 0.9-2.1 ms |
  | one repaint when the view settles, on the page (before) | 24-26 ms | 49-91 ms | 41-70 ms |
  | the same repaint in the worker (after) | 29 ms | 60-98 ms | 52-79 ms |
  | page work to put the worker's picture up | 0.1 ms | 0.1-0.2 ms | 0-0.1 ms |

  The repaint was the only thing over the 50 ms line. It hitched the map
  every time the view stopped, so it moved off the page's thread (4d). No
  frame the page is responsible for now costs more than about 2 ms. The
  owner is still best placed to say how it feels in a normal window.

## Rejected alternatives

- **A tile server** would be a location report on every page load.
- **Natural Earth for everything:** 1:10,000,000 is miles off at local zoom.
- **Projecting in the browser:** that would be two implementations of one
  rule, which ADR 0015 already refused.
- **Paths inside the svg:** a pan repainted every vertex on every frame.
- **Capping the zoom at 20 px/mi:** pins in neighbouring towns could no
  longer be separated.
- **Secondary and local roads, and water per county:** these are
  county-by-county files, gigabytes in total.
- **OpenStreetMap:** a licence decision for the owner, and not needed to
  meet the bar.

## Consequences

- `data/` grows from 699 KB to 8.3 MB, all of it public domain and
  reproducible with one command.
- The server holds about 42 MB more while it runs.
- Six town points moved. Anyone who has already run `jsa discover` should
  run `jsa rescore`.
- Two scanner exemptions: the basemap files join the Census town and ZIP
  files in `SKIP_PERSONAL_FILES`. A few million coordinate integers are
  bound to spell somebody's ZIP code or part of a phone number. Key,
  email and home-path detection still run on them.
- `tests/test_map.py` now checks imports of the build tool rather than
  mentions of its name, so `jsa doctor` can tell a reader how to run it.
