# ADR 0017 — What folds into one card

Status: accepted
Date: 2026-09-27
Relates to: ADR 0014 (distance), ADR 0015 (the map).

## Context

This goal was asked for as "merge the duplicates": my report was that
same-title SpaceX postings in Hawthorne and El Segundo were piling up. That
report was wrong. It came from counting rows in `jobs`, not cards on the page.
Every same-company, same-title group (96 of them, 239 rows) already shared a
`dedup_key`, and `v_new_matches` already folded each into one card. Titles
shown twice on the dashboard: **0**.

The measurement found the opposite problem. `dedup_key` stripped *every*
parenthetical from a title, because it was written for regional copies of one
req — "(Korea)", "(West Coast)". SpaceX puts the **team** there:

| one card, `5:software engineer`, folded 36 postings with 18 titles |
|---|
| Software Engineer (Starlink), (Platform Team), (Flight Reliability), (AI Data Engineering), (Application Software), (Starshield), (Controls Software), (Starlink Ground Network)… |

Across the tracker, **69 cards folded more than one distinct title, hiding 139
of them.** `db.find_duplicate` had already written down why that is the worse
mistake: *a wrong merge hides a job the operator would have seen, which is
worse than showing one posting twice.*

## Decision

### 1. A qualifier folds only when it says where

`scoring.regional_qualifier(qualifier, location)` decides. A parenthetical is
regional when it is:

- an arrangement or region word — Remote, Hybrid, US, EMEA, West Coast, Bay
  Area, Middle East;
- a foreign country or city (the list ADR 0002 already maintains), or a US
  state;
- a "City, ST" that resolves;
- **or a bare name for where the posting itself is**: whole-word in the
  posting's own location field, or a place within 30 miles of it.

Anything else is part of which job it is, and stays in the key.

### 2. Why the place data alone cannot decide it

Looking a bare name up in the Gazetteer was measured first, over all 203
distinct parentheticals, and it was wrong in the dangerous direction. "(Falcon)"
is SpaceX's rocket program and also Falcon, Colorado — seven postings would
have stayed hidden. "(AI)", "(Oil and Gas)", "(Farmer)" and "(Python, Java,
Rust, C#, C++)" are all real towns somewhere. A team name that happens to be a
town is not rare enough to ignore.

What decides it is the posting's own location. "Deployed Engineer (Chicago)"
located in Chicago restates where it is. "Software Engineer (Falcon)" located
in Hawthorne does not. When the rule is unsure, it keeps the card: an extra
card is the recoverable mistake.

**Measured on the live tracker:** every one of the 36 qualifiers it folds is a
real place, region or arrangement, and every known team word stays distinct.
**Cards: 1,233 → 1,336.** The largest remaining folds are all one req in many
cities — LangChain's "Deployed Engineer" in 11, SpaceX's "Software Engineer
(Platform Team)" in 7.

### 3. A folded card is judged by its nearest copy

Those remaining folds are exactly where "is there one near me?" matters. The
radius filter judged a card only by its own row — whichever copy scored best
from the profile's home — so "Software Engineer (Starlink)" was hidden within
25 miles of Redmond, WA despite having a copy there. It now keeps a card when
any copy is inside the radius, shows the distance to the nearest one with
"(its nearest location)", and treats a remote copy as making the whole card
remote.

The map draws every copy rather than one per card, so n19's property still
holds per card: a card is kept if and only if it has a copy inside the circle.

### 4. The company cap comes after the radius

The page shows at most three cards per employer. That cap was applied in SQL,
*before* the radius: a company's three slots went to its best cards anywhere,
the radius hid them, and the one near you had already been capped out. It is
now applied after. Measured at 40 miles:

| home | nearby cards before | after |
|---|---|---|
| Los Angeles, CA | 34 | 37 |
| Boston, MA | 1 | 2 |
| Austin, TX | 2 | 6 |
| Seattle, WA | 10 | 15 |

The page got faster, not slower: ~190 ms against ~280 ms, because the window
function it replaced was the expensive part.

## Two bugs found on the way

- **The list and the map disagreed at the edge of the circle.** The list
  compared the *rounded* mileage with the radius, the map the exact one, so a
  job 25.4 miles away was kept by a 25-mile radius and drawn outside the
  circle — the one disagreement ADR 0015 said could not happen. The comparison
  is now exact everywhere; only the display is rounded.
- **A view change never reached an existing tracker.** `db.migrate` rebuilt
  views only when a *table* had changed, so the column this goal added to
  `v_new_matches` silently never appeared on a tracker that already existed.
  No test could see it, because every test starts from an empty database.
  Views are now rebuilt on every upgrade; they hold no data.

## Consequences

- `dedup_key(company, title, location)` takes the location. `db.upgrade()`
  recomputes every stored key, idempotently — 504 rows changed on the author's
  tracker, a second run changes none — so nobody has to run anything.
- **Not changed:** `jsa enrich` still spends one model call per copy, about 120
  of ~620 calls so far on copies that fold into another card. Measured, 115 of
  129 folded groups have *different* text across their copies, so skipping
  them could lose facts. It is efficiency on a free tier, not correctness.
