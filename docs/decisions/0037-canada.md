# ADR 0037 — Canada, the second country pack

Status: accepted 2026-11-07 (built 2026-10-30; the owner looked at a
Toronto profile in the dashboard)
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
   Canada ships its own employer list, `jsa/resources/companies_ca.yaml`:
   all 22 measured employers, the owner's choice (2026-10-30). Each board
   was confirmed as that employer's (Greenhouse's board name; for Lever and
   Ashby, every posting names the company or its product) and passed
   `jsa verify` (22 of 22). A clone working in Canada reads and verifies
   that file; the US list is untouched.
8. **The national map** uses Canada Albers (parallels 50 and 70, origin
   40 N 96 W) over the province outline. The live map has Canada's own
   ground since 2026-11-07 (below).

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

## Checked in the dashboard, 2026-11-07

A scratch home (`JSA_HOME`) with the example profile set to Canada: postal
area M5V, 50 km, remote in Canada, five target roles. `jsa init` seeded
Canada's employer list, and one `jsa discover` read the 22 boards: 795
postings fetched, 90 kept (16 employers with at least one). The dashboard
showed kilometres, the map centred on M5V with drive-time rings, "anywhere
in Canada", pay in the working country's dollars, the degree line and no
role education line, with no console errors. The owner looked and accepted.

## The ground under the live map, 2026-11-07

The owner asked for a Canadian map that looks exactly like the US one. It is
built by `tools/build_ca_data.py --only basemap` into
`data/ca/ca_basemap_{coarse,medium,fine}.json.gz`, in the US files' exact
shape: the same three tiers, scales, simplification and layers
(`tools/build_map_data.py` TIERS, ADR 0019), so the page and `jsa.basemap`
draw it unchanged.

| Layer | Source |
|---|---|
| land | Provinces, cartographic boundary file (shore-clipped): the outer rings |
| lake | The same file's holes (the large lakes it cuts out), plus Natural Earth's 10m lakes that lie on Canada's land and in no hole |
| urban | Population centres |
| county (fine only) | Census divisions, without islands under 1% of a division's largest piece (their shore is the land's own) |
| road | Road Network File: rank 1 (the Trans-Canada and freeways) at the coarse tier, ranks 1-2 at the others |
| city names | Natural Earth populated places, 50,000 people or more, each on Statistics Canada's point for that town (4 aliases: Greater Sudbury, Trois-Rivières, Saint-Jérôme, Saguenay for Chicoutimi) |

Fetched once from Statistics Canada's map service at half a pixel of the
fine tier (0.000362 degrees), one province at a time (the whole country at
that detail is refused), and simplified per tier from that.

Sizes: coarse 573 KB, medium 2.3 MB, fine 8.9 MB (the US: 0.3, 1.5 and
6.2 MB). The fine land is 1.8 million points against the US's 0.2 million:
the Arctic islands. 42 city names, all on a town.

Checked in the dashboard on the Toronto preview: Lakes Ontario, Erie and
Simcoe, the 400-series highways and the population centres at metro
zoom; the Toronto Islands at the fine tier; the provinces, Hudson Bay and
the Montreal, Ottawa and Quebec City labels zoomed out; no server or console
errors. Side by side with the US dashboard, the same colours and strokes.

What it does not have that the US has: the fine tier's smaller lakes are
Natural Earth's (1:10,000,000), not a national water file, so up close they
are about a kilometre off; the US fine tier draws its lakes from the
Census's own water areas. The map shows no ground across the border, as the
US map shows none in Canada.

## Not done

- French pay text ("80 000 $") and French page wording.
- Role education data for Canada (Statistics Canada publishes a candidate
  table; not checked).
