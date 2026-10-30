# ADR 0037 — Canada, the second country pack

Status: built 2026-10-30; accepted when the owner has looked at it in the
dashboard
Date: 2026-10-30
Relates to: ADR 0036 (country packs), ADR 0013 and 0029 (nationwide
sources), ADR 0020 (postings the tool may not read), ADR 0006 (pay).

## Owner's decisions (2026-10-30)

1. **Kilometres** for a Canadian profile; miles stay for the US. Built in
   phase 2: the app measures in miles, the pack picks the unit shown and
   typed, and the profile key is `radius` (`radius_miles` still read).
2. **Postal-code areas only**: the first three characters (a forward
   sortation area, "M5V"). Full six-character codes are never stored or
   looked up.
3. **No nationwide source at first**: employer boards and a checked list of
   Canadian employers.
4. **English first**: accented place names (Montréal, Québec) match from
   the start; French pay text ("80 000 $") is a later step.

## Measurements (phase 1, 2026-10-30, read-only)

### Job Bank: refused

Job Bank's job-seeker terms (page dated 2026-09-24) prohibit "any script,
robot, spider, Web crawler, screen scraper, automated query program,
artificial intelligence or other automated device, software, or process to
access its services". It joins LinkedIn, Indeed, Glassdoor and YC (ADR
0020): never read, and `jsa find` never requests it. Its monthly open data
has no employer names, descriptions or links, so it is no job source.

### Places, postal areas and the outline: one source

Statistics Canada's 2021 cartographic boundary map service
(`geo.statcan.gc.ca/geo_wa/rest/services/2021/Cartographic_boundary_files/MapServer`)
answers plain queries, so no bulk download is needed:

| Layer | Rows | Use |
|---|---|---|
| 14, forward sortation areas | 1,643 (all returned) | postal-area centre points |
| 9, census subdivisions | 5,161 | place names with a province |
| 7, population centres | 1,030 | not needed (see below) |
| 0, provinces and territories | 13 | the national outline |

Checked on one area: M5V's simplified outline averages to 43.63 N,
79.41 W, downtown Toronto. Licence: the Statistics Canada Open Licence
(use, redistribute, adapt, sell). A product built from it carries "Adapted
from Statistics Canada, <product>, <reference date>. This does not
constitute an endorsement by Statistics Canada of this product." The FSA
file contains data "copied with permission from Canada Post"; the postal
code is Canada Post's official mark. The NRCan names database (16 MB) is
not needed.

Place matching on the 578 Canadian postings below (94 distinct location
strings): census subdivisions placed 418, population centres 351, both
together 418. Everything left unplaced was country-wide or remote
("Remote (United States | Canada)" 59, "Canada" 24, "Canada - Remote" 12,
"Ontario - Remote" 9…), which is correct, except "Scarborough, ON" (4), a
former city inside Toronto: an alias. Decision: census subdivisions plus a
short alias list.

### Job sources

- **Employer boards.** The 36 shipped Greenhouse, Lever and Ashby boards
  carried 130 postings located in Canada (most at Cohere 43 and Sierra 23).
  Of about 50 guessed Canadian employers, 28 answered on some board, 22
  with Canadian postings: Lightspeed 74, 1Password 65, Neo Financial 59,
  PointClickCare 44, Jobber 29, Later 21, Benevity 21, Float 20,
  Trulioo 18, D2L 16, Geotab 16, Faire 14, Achievers 14, Procurify 10,
  Klue 7, Hopper 7, Clearco 5, Rewind 3, Wattpad 2, Hootsuite 1,
  Unbounce 1, Visier 1 (448 together). 578 Canadian postings in all. A guessed token can
  be someone else's board (companies.yaml's warning), so each one is
  confirmed with `jsa verify` and, for Greenhouse, its board name before it
  ships; the owner picks which.
- **The Muse.** Sampling 100 postings per city: Toronto 23 in Canada,
  Montréal 3, Calgary 3, Vancouver 0, Ottawa 0; the rest were remote
  postings already reachable. Not used for Canada.

### Pay text

Of the 578 postings, 202 state pay in a sentence about salary or
compensation, and today's parser reads 151 of them (75%). The 51 it misses:

- "$100,000 TO $140,000 CAD" (an upper-case TO, 13, one employer);
- "a minimum annual salary of $X, a midpoint of $Y, and a maximum of $Z"
  (14, one employer);
- one "per month", one "OTE" range, and the rest not pay at all on a
  closer look (deal sizes, revenue).

Not seen in these postings: "CAD $90,000" with the code first, "$120k", or
French "80 000 $". The plan's phase 5 is revised to the shapes measured.
199 postings mention CAD and 73 mention USD: some employers post a US range
on a Canadian location, so an explicit "USD" must stay USD in a Canadian
profile, never be read as CAD.

### Pay, after phase 5

With the Canada pack and the three fixes (an upper-case TO, minimum and
maximum written out, a currency code after a figure), 183 of the 202
stated pay figures read, up from 151. Left: one "OTE" range, one per-month
figure, and two figures that are not pay. The same fixes moved 3 of the
2,219 postings in the owner's US tracker (read-only check, 2026-10-30),
each from USD to the CAD the posting states.

## Decision

1. **`jsa/countries/ca`** is the second pack, filled from the measurements
   above. A profile says `country: CA`.
2. **Places** are Statistics Canada's census subdivisions, each placed on
   the smaller of its own area and the population centre of the same name;
   a town beats a parish of the same name. Accents fold ("Montreal"), a
   French hyphenated name is never cut into an alias ("Saint"), and
   Toronto's former cities and Quebec City are aliases.
3. **Postal areas only.** A home "M5V 2T6" is looked up and stored as
   "M5V"; the data holds nothing finer. The resume import still removes a
   full postal code wherever it appears.
4. **Home and abroad.** A location naming Canada or a province is home,
   checked first, so "Remote (United States | Canada)" stays. The US (its
   name, states, big cities, "City, ST", "Remote (US)") and the rest of the
   world are abroad. State codes that are also words after a comma (IN, OR,
   ME, HI, OK) and CA are left out of "City, ST".
5. **Pay** is CAD with the US pay band, which the measured Canadian figures
   fit. A figure followed by another currency's code keeps that currency
   and is not compared with a CAD floor.
6. **Kilometres** (default radius 65 km), "legally entitled to work in
   Canada", Letter paper, year-first numeric dates, no role education line.
7. **No nationwide source.** Job Bank is refused; The Muse is left out.
   Until the owner picks Canadian employers, a new Canadian install starts
   from the shipped list, and its Canadian postings score.
8. **The national map** uses Canada Albers (parallels 50 and 70, origin
   40 N 96 W) over the province outline; no ground layer is built yet, so
   the live map draws without one.

Three pack names made this possible without touching the US:
`FOLD_ACCENTS`, `ALIAS_SPLIT` and `POSTAL_AREA_ONLY`.

## How it is held

- `tests/test_canada.py`: places, scoring, pay shapes, setup, the map, and
  every pack's contact samples removed in every working country.
- `tests/test_canada_e2e.py`: a made-up Toronto profile over Canadian and
  US postings, scored and rendered.
- `tests/test_country_guard.py`: Canadian strings (Canada, CAD, provinces,
  km, postal areas) only inside the Canada pack, as US ones are only in
  the US pack.
- The US golden pages are identical to before Canada, apart from the map
  script's unit factor (phase 2), and the US baseline is unchanged.

## Not done

- French pay text ("80 000 $") and French page wording.
- A Canadian employer list (the owner picks from the 22 measured).
- A ground layer under the live map (roads, water).
- Role education data for Canada (Statistics Canada publishes a candidate
  table; not checked).
