# ADR 0018 — The Matches page as a live map

Status: accepted; its map gained ground under it in ADR 0019
Date: 2026-09-28
Relates to: ADR 0014 (distance, not place names), ADR 0015 (a map that
agrees with its filter), ADR 0017 (what folds into one card).

## Context

The owner had a mock-up built, a standalone React page on invented data, of
what the Matches page could be: the map beside the list, a radius that
answers while you drag it, commute-time bands, markers clustered and
coloured by application status, and analytics of the area. They asked for
it in the real dashboard.

Three things about the real dashboard shaped how:

- It is **server-rendered with no build step**, on purpose (the header of
  `jsa/web.py`). React would bring Node, a bundler and a committed build into
  a Python project, for everybody who installs it.
- Its map's one promise (ADR 0015) is that **the picture cannot disagree with
  the filter**, and its script said so: *"It must never decide which
  postings match."*
- Matches listed only postings with **no application**, so there was no
  status to colour anything by.

## Decision

Decided with the owner on 2026-09-28:

1. **Plain JavaScript in the existing page.** No dependency, no build, no
   request to any other host: no CDN, no web fonts, no tiles. With
   JavaScript off the page is exactly what it was, the form, the server's
   map and the server's list.
2. **Live applications appear on Matches.** Saved, drafting and ready are
   *saved*; applied is *applied*; phone screen, technical, onsite and offer
   are *interview*. Each card's pill names the real stage; the group is only
   its colour. Rejected, withdrawn and ghosted never appear. The
   three-per-company cap is for new matches only: it exists to stop a board
   with four hundred roles filling the page, and your own applications are
   not that. Nothing on this page changes a status.
3. **The radius keeps its range**, 5 to 300 miles plus *anywhere*, because
   that is what the profile's radius is checked against.

### How the browser stays out of the decision

While the handle moves, the script redraws the circle and counts the dots
inside it. It judges each dot by `d`, the distance `places.nearest` returned
on the server. That is the same float the radius filter compared, sent as
JSON (which carries a Python float exactly), and compared the same way,
`d <= radius`. The dot's x and y place it on screen; they never decide
anything. In an azimuthal-equidistant projection about home, the length of
(x, y) *is* the distance, so the drawn circle agrees with `d` to within the
rounding of x and y.

When the handle settles, the script asks the server for the page at the new
radius and swaps the list in, without a reload. So the list is always the
server's answer, with the cap, folding and the remote rule, all of which
stay in Python alone. The count in the panel is postings inside the circle.
The list is cards, folded and capped. The page labels each for what it is.

Status chips, sorting and the analytics **rearrange what the server sent and
never add to it**. They are a view, like scrolling.

### Commute times are estimates, and say so

There is no routing service and there will not be one: it would learn where
the operator lives. A time is straight-line miles × a typical detour ÷ an
average speed, plus a start-up time:

| mode | detour | speed | start-up |
|---|---|---|---|
| drive | ×1.25 | 32 mph | 4 min |
| transit | ×1.35 | 13 mph | 12 min |
| bike | ×1.3 | 11 mph | 2 min |
| walk | ×1.25 | 3 mph | 0 |

Every place a time appears says "estimated", and the panel explains the
formula. These are presentation constants in the page's script and decide
nothing about which jobs are shown.

### What the map data does not carry

The data the script reads holds every placeable posting, inside the circle
or not, but **no titles**. `tests/test_radius` holds that a posting the radius
hides is not on the page at all, and it caught the first version, which
carried them. A marker that belongs to a listed card takes its title from
the card; any other is "a posting in Denver", and clicking it opens the
job page.

## Measured on the author's tracker, 2026-09-28

Within 40 miles of the profile's home: 476 postings on the map inside the
circle, 424 remote with no distance, 67 naming a place the data does not
have. At 15 miles: 348 inside, and the refreshed list held 60 new matches
and 3 applications, none of the placed ones beyond 15 miles.

**Pay is thin: 1 of the 63 listed cards states a salary.** The pay panel
says "1 of 63 state it" rather than drawing a chart from one number. That is
a fact about the postings, not the page.

## Consequences

- `v_new_matches` exposes `salary_min`, `salary_max` and `salary_period`.
  Views are rebuilt on every upgrade (n20), so nobody runs anything.
- `mapview.points()` is the geometry for the live map, beside `local()` and
  `national()`. `tests/test_live_map.py` pins it: `d` is `places.nearest`
  exactly, and the length of (x, y) matches `d`.
- The page is about 40 KB larger with JavaScript, and has no dependencies.
- Moving Home is still done by typing a place and pressing Filter.
  Dragging it would mean distances measured in the browser, and the browser
  does not measure.
