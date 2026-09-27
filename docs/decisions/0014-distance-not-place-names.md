# ADR 0014 — Distance, not place names

Status: accepted
Date: 2026-09-26
Relates to: ADR 0002 (geographic scope), ADR 0009 (the shipped feed list).

## Context

Location scoring was string matching. A posting scored full marks when its
text contained a city the operator had written into `locations`, less when it
named a state they had listed, and zero otherwise. Somebody in Los Angeles
had to list Example Town, Example Town, Example Town and every other suburb by hand,
or those jobs scored zero. A `regions` block existed to group those towns for
`matches --near`, which is the same list written twice.

It took three goals (n7, n9, n17) to make that work outside California, and it
still could not answer the question anybody actually asks: **is this an hour
away?**

Since n16 the tracker also receives postings from everywhere, so "near me" is
now the filter that matters rather than a nicety.

## Decision

**Place both ends on a map and rank by miles.**

1. **The data ships with the tool.** Census Gazetteer centroids, public
   domain: 32,109 towns and 33,791 ZIP codes, 686 KB gzipped, in `data/`.
   No geocoding service. One would need a key, and it would send every
   posting's location and the operator's home ZIP to a third party. A home
   ZIP is identity, and this project keeps identity local.
2. **The origin is `home_location`** — a ZIP or "City, ST" — and the reach is
   `radius_miles`, default 40. Both are optional: unset, the origin is the
   first real place in `locations`, so **an existing profile gains distance
   scoring without being edited**.
3. **Where distance sits in the order** (`scoring.location_score`):
   - outside the US → rejected, as before (ADR 0002), before any arithmetic;
   - remote → scored by the remote rules, never given a distance;
   - a city the operator **listed** → 1.0, whatever the mileage. An explicit
     choice outranks arithmetic about it;
   - otherwise, if both ends place: 1.0 inside the radius, 0.65 to twice it,
     0.35 to five times it;
   - a state they listed → at least 0.6, and the reason says both ("182 miles
     away, in Texas, a state you listed");
   - beyond five radii and never listed → 0.0. Distance must not become a
     floor under jobs across the country.
4. **A posting that cannot be placed scores 0.2 and says so.** It is not
   rejected and never given a guessed distance. `jsa doctor` reports how many
   there are.

### What this is measured at

Of 1,224 open postings on 2026-09-26: **91.2% are usable by a radius**
(84.9% placed, 4.2% remote, 2.1% both). 7.9% cannot be placed — mostly
postings naming a country, a department instead of a town, or a town too new
for the 2024 Census file (Starbase, TX, incorporated 2025, is 25 of them).

For the author's own profile the effect is immediate: Example Town, never listed
anywhere, now scores 1.0 at 15 miles.

## What happens to `regions` and `matches --near`

**They stay, and they are no longer the way to say where you live.**

`regions` is now one thing only: named groups for the `--near` filter and the
dashboard's region dropdown, which are *display* filters the operator drives
by hand. Scoring no longer consults them for anything a radius can answer.

They were not deleted because they do something a radius cannot: name a set
of places that is not a circle — "the towns along this train line", "the two
cities I would relocate to". Deleting them would have forced those operators
to pick a radius that includes places they do not want.

They are, however, no longer necessary, and the example profile no longer
presents them as the way to describe where you are.

## Rejected alternatives

- **A geocoding API.** Better coverage of messy strings, at the cost of a
  key, a network dependency, and sending the operator's home ZIP to a
  stranger. Not worth it for the 8% it would recover.
- **Placing a posting at its state's centre when only a state is named.** It
  would make coverage look better and put jobs tens or hundreds of miles from
  where the operator thinks they are. Unplaced is honest; roughly-placed is
  not.
- **Rejecting anything outside the radius.** The years filter learned this
  already (ADR 0013's sibling decision): rank it lower and let the operator
  decide whether the drive is worth it.
- **Replacing `regions` outright.** See above.

## Consequences

- `jsa rescore` re-scores stored postings with distance. It must be run once
  after this lands, or old scores stay until the next discovery run.
- Two new optional preferences, both defaulted, so no profile is invalidated.
- The shipped data is public reference data that necessarily contains every
  US ZIP and town — including the operator's own. The secret scanners had to
  learn that: they now decompress `.gz` rather than reading compression noise
  as text, and they skip personal matching on those two files while still
  checking them for keys.
- A radius control and a map on the dashboard (n18, n19) are now worth
  building; the 91.2% measurement is the evidence.
