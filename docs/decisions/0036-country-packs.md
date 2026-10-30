# ADR 0036 — Country packs

Status: accepted; the US is the only pack (2026-10-29). Canada added as
the second, 2026-10-30 (ADR 0037).
Date: 2026-10-29
Relates to: ADR 0001 (the pay floor, renamed here), ADR 0006 (pay
extraction), ADR 0013 and 0029 (the nationwide sources), ADR 0019 (the
basemap), ADR 0022 (resume import), ADR 0033 (role education data).

## Context

The app was written for one person in the US, and the US was assumed at
every level. A read-only sweep on 2026-10-29 found it in:

- **places**: three hand-copied tables of US states, "City, ST" grammar,
  5-digit ZIPs, Census place and ZIP files;
- **scoring**: a hard reject for anywhere "outside the US", built from a
  US signal and a list of about 100 foreign places, and a "your state"
  bonus;
- **pay**: a parser anchored on a literal `$`, US-sized plausibility bounds,
  a 2,080-hour year, and a profile key named `compensation_floor_usd`;
- **the resume import**: US phone, ZIP, street and "City, ST" patterns,
  which are also its privacy guard (what they find is removed before any
  text reaches the model);
- **forms, sources, the map, role data, resumes and page wording**:
  "Remote (US)", the US work-authorization question, USAJOBS and The Muse,
  the US Albers projection and outline, BLS and O*NET, Letter paper, and
  "ZIP", "City, ST", "in the US" throughout.

Adding another country meant first making the US one country among many,
without changing anything for the existing US user.

## Decision

1. **One pack per country** in `jsa/countries/<code>/`. A pack is a plain
   module: constants and a few small functions, no other `jsa` imports (so
   the build tools can read it). The US pack holds everything listed above.
2. **One home country per profile**: `job_search_preferences.country`, a
   two-letter code, default `US`. An unknown code is a plain ConfigError.
   Loading the live profile sets the working country
   (`countries.activate`); a draft being read does not. Everything
   country-specific asks `countries.active()` **at call time**, and every
   cache of country data is keyed by country.
3. **The pay floor is `compensation_floor`**, in the profile's own
   country's currency (there is no separate currency key: one country per
   profile). `compensation_floor_usd` is still read; setting both is an
   error; every floor message names the key the profile actually uses.
4. **Privacy across countries.** The resume import tries the working
   country's contact patterns first (they fill the draft) and every other
   pack's too (they only remove), so a resume written elsewhere is still
   cleaned before the model sees it.
5. **Sources belong to a country or to none.** A pack lists the sources
   that search its country (`SOURCE_KINDS`); discovery skips a source
   another pack lists. Employer boards (Greenhouse, Lever, Ashby, Workday…)
   belong to no country and run everywhere.
6. **Optional data stays optional.** A pack without role education data
   (`ROLE_EDUCATION = False`) shows no line, never another country's
   numbers; a pack without a map outline gets no national map.

## What a pack holds

| Area | Names (US values) |
|---|---|
| Identity | `CODE` "US", `NAME`, `IN_TEXT` "the US", `ADJECTIVE` "US" |
| Regions | `STATES`, `TERRITORIES`, `SUBDIVISIONS`, `REGION_BY_NAME`, `REGION_WORD` "state", `SCORING_REGIONS` |
| Places | `PLACE_ALIASES`, `FIRST_WORD`, `COUNTRY_NOISE`, `POSTAL_CODE`, `POSTAL_COLUMN`, `postal_key()`, `POSTAL_NAME` "ZIP", `POSTAL_EXAMPLE`, `PLACE_FORM` "City, ST" |
| Scoring | `HOME_SIGNAL`, `ABROAD`, `REGION_WORDS`, `TITLE_REGION_SUFFIX` |
| Pay | `CURRENCY` "USD", `CURRENCY_SYMBOL` "$", `HOURS_PER_YEAR` 2080, `ANNUAL_BOUNDS`, `HOURLY_BOUNDS`, `PAY_LOW`, `PAY_HIGH` |
| Distance | `DISTANCE_UNIT` "mile", `DISTANCE_UNITS` "miles", `DISTANCE_SHORT` "mi", `MILES_PER_UNIT` 1.0, `DEFAULT_RADIUS` 40 |
| Forms | `REMOTE_LABEL` "Remote (US)", `AUTH_QUESTION`, `AUTH_EXAMPLE`, `AUTHORIZED_KEY` |
| Privacy | `CONTACT_PHONE`, `CONTACT_POSTAL`, `CONTACT_CITY_REGION`, `CONTACT_STREET`, `CONTACT_SAMPLE` |
| Sources | `SOURCE_KINDS`, `SEED_COMPANIES` |
| Map | `MAP_OFFSHORE`, `MAP_PARALLELS`, `MAP_ORIGIN`, `MAP_MAINLAND`, `MAP_OFFSHORE_NAMES`, `OUTLINE_FILE`, `basemap_file()` |
| Data | `DATA`, `PLACES_FILE`, `POSTAL_FILE`, `ROLE_EDUCATION` |
| Resumes | `PAPER` "letter" (or "a4"), `NUMERIC_DATE` |

## Adding a country

1. Measure first: which public sources give its places and postal codes
   (and their licences), which job sources search it and on what terms,
   what its postings' pay text and contact formats look like.
2. Copy `tests/testland.py` (a complete fictional pack) to
   `jsa/countries/<code>/__init__.py`; fill every name with that country's
   values and `CONTACT_SAMPLE` with fictional ones. Build its data files
   into `jsa/resources/data/<code>/` with a tool under `tools/`.
3. Register it in `jsa/countries/__init__.py` `PACKS`, add the package to
   `pyproject.toml`, and give its `ABROAD` and `HOME_SIGNAL` some thought:
   from Canada, Toronto is home and Texas is abroad.
4. Run the suite. `tests/test_testland.py` shows the shape of an
   end-to-end test; the privacy test removes every pack's samples.

## How it is held

- `tests/test_country_baseline.py` pins today's US results (places,
  scoring, pay, redaction) to `tests/fixtures/us_baseline.json`; every
  step of the split left it and the 25 golden pages (`tests/golden_pages.py`)
  byte-identical.
- `tests/test_country_guard.py` fails if a string outside `jsa/countries`
  names a dollar sign, USD, the US, a ZIP, "City, ST", "Remote (US)", the
  lower 48 or a US state, with two allowances, each with its reason.
- `tests/test_testland.py` runs the app in a fictional country.

## Consequences

- Nothing changed for a US profile; an existing profile needs no edit.
- Not yet moved, and to be done with the first real country: the map's pay
  bands in `matches.html` ("under 60k"…) are US-sized; the extension's
  field matching is English and says "state". Distances moved on
  2026-10-30 (Canada plan, phase 2): the app still measures in miles, and a
  pack's `DISTANCE_UNIT(S)`, `DISTANCE_SHORT`, `MILES_PER_UNIT` and
  `DEFAULT_RADIUS` decide what a person types and reads; the profile key is
  `radius`, with `radius_miles` still read (always miles).
- The live map's nationwide view needs a pack's outline; until one is
  built, a country without one shows the local map only.
