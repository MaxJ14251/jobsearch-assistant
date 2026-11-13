# ADR 0037 — Canada, the second country pack

Status: accepted 2026-11-07 (built 2026-10-30; the owner looked at a
Toronto profile in the dashboard); second pass 2026-11-11
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
| lake | The same file's holes (the large lakes it cuts out), plus Natural Earth's 10m lakes that lie on Canada's land and in no hole; at the fine tier, the Natural Earth lakes within 10 km of a population centre are redrawn from NRCan CanVec 1:250,000 waterbodies (second pass, below) |
| urban | Population centres |
| county (fine only) | Census divisions, without islands under 1% of a division's largest piece (their shore is the land's own) |
| road | Road Network File: rank 1 (the Trans-Canada and freeways) at the coarse tier, ranks 1-2 at the others |
| city names | Natural Earth populated places, 50,000 people or more, each on Statistics Canada's point for that town (4 aliases: Greater Sudbury, Trois-Rivières, Saint-Jérôme, Saguenay for Chicoutimi) |

Fetched once from Statistics Canada's map service at half a pixel of the
fine tier (0.000362 degrees), one province at a time (the whole country at
that detail is refused), and simplified per tier from that.

Sizes: coarse 573 KB, medium 2.3 MB, fine 8.9 MB (9.3 MB after the second
pass's lakes; the US: 0.3, 1.5 and
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
Census's own water areas. (The second pass redraws those near towns from
CanVec; see below.) The map shows no ground across the border, as the
US map shows none in Canada.

## Second pass, 2026-11-11

Measured on the 22 employers' boards (727 postings, 407 of them in Canada)
and 30 real Canadian application forms. The owner's US results were
checked before and after with a read-only copy of the tracker: 0 of 2,488
scores, reasons or pay readings changed.

**Places.** A list of towns joined by commas ("Vancouver, BC, Toronto, ON")
places every town, in Canada only (`CITY_LISTS`); "US - Remote", "Remote
in the US" and more European cities count as abroad; Recruitee keeps the
province.

| Board | Postings | Placed in Canada | Abroad | Unplaced |
|---|---|---|---|---|
| Ashby | 422 | 250 | 75 -> 89 | 25 -> 11 |
| Lever | 108 | 57 | 47 | 0 |
| Greenhouse | 197 | 60 -> 62 | 120 | 4 |

The unplaced left are bare towns two provinces share (Red Deer,
Lethbridge, by design) and "Remote" or "Multiple".

**Forms.** Prepared answers ask "Are you legally entitled to work in
Canada?" and name the currency when it isn't Canadian dollars. The
extension recognised 314 of the 30 forms' questions before and 329 after:
the province (a dropdown gets the province's name), "City & Country",
"Where are you currently located?" and "legally entitled". A yes/no that
names another country ("authorized to work in the United States?") is left
for the person, in both countries. None of the 30 asked for a postal code.

**French.** 28 of the 727 postings are in French (27 Lightspeed, 1 Neo).
- Pay written the French way ("80 000 $ à 95 000 $", "20,00 $/heure") is
  read after the English reading finds nothing (`FRENCH_PAY`). One posting
  states pay that way, and it is now read; pay that includes commission
  ("rémunération cible") is not taken as base.
- Titles are scored in English (`title_in_english`): French words become
  English, the role noun moves to the end, a bilingual title uses its
  English half, and the senior and manager rules read the result. Against
  the 18 French postings that have an English twin, the score was within
  0.03 of the twin's for 4 before and 13 after, and the title's verdict
  agrees for 16. The two that differ are wording: "Gestionnaire, X" is a
  people-manager title as "Manager, X" is, while the twin says "X Manager";
  "Analyse de données" names the field, not the analyst.
- "5 ans d'expérience" counts as five years (`YEARS_ASKED`).
- Owner's decision: score French postings fairly (option A); the pages
  stay in English.

**Who holds each kind of job.** Statistics Canada's 2021 Census table
98-10-0449-01 (highest certificate by NOC 2021 occupation; employed, 25 and
older, Canada) and the NOC 2021 V1.0 titles, built by
`tools/build_ca_data.py --only roles` into the US files' columns: 512
occupations, 25,945 titles. The second share is "trades, college or CEGEP":
Canada has no "some college, no credential" row (those people count as
high school), and its trades certificates sit where the US has some
college. Eight titles postings use differently are set by hand, as in the
US build. Checked: support engineer and technical support specialist (user
support technicians, not wholesale technical sales), software engineer,
customer service representative, data analyst, registered nurse,
electrician, accountant. Questionable, kept because it is NOC's own:
"Product Manager" is filed under corporate sales managers. Adapted from
Statistics Canada, Table 98-10-0449-01 and NOC 2021 Version 1.0, under the
Statistics Canada Open Licence; the about page says so.

**The 22 employers, as US employers are checked.**

| Employer | Board | Postings | In Canada | Pay found |
|---|---|---|---|---|
| Lightspeed | Ashby | 129 | 88 | 30% |
| Neo Financial | Ashby | 59 | 57 | 79% |
| PointClickCare | Lever | 77 | 46 | 91% |
| Jobber | Ashby | 38 | 29 | 100% |
| Benevity | Ashby | 23 | 21 | 43% |
| Trulioo | Ashby | 28 | 20 | 100% |
| D2L | Greenhouse | 25 | 19 | 95% |
| Later | Greenhouse | 30 | 18 | 83% |
| Geotab | Greenhouse | 62 | 17 | 100% |
| Float | Ashby | 16 | 16 | 75% |
| Achievers | Lever | 14 | 13 | 62% -> 85% |
| Faire | Greenhouse | 69 | 12 | 100% |
| Hopper | Ashby | 21 | 9 | 78% |
| Procurify | Ashby | 8 | 8 | 75% |
| 1Password | Ashby | 80 | 7 | 100% |
| Klue | Ashby | 9 | 7 | 100% |
| Hootsuite | Greenhouse | 11 | 7 | 86% |
| Clearco | Ashby | 6 | 6 | 50% |
| Rewind | Ashby | 3 | 3 | 67% |
| Wattpad | Lever | 17 | 2 | 100% |
| Visier | Ashby | 2 | 2 | 0% |
| Unbounce | Greenhouse | 0 | 0 | — |

Pay is found for 72% of the Canadian postings, 73% after this pass. Every
missing one was read by hand: the postings state no pay (Lightspeed 57,
Benevity 12, Neo 10), or the only dollar figures are benefits, funding
rounds or an "up to" across levels. One shape was a miss and is fixed:
Achievers heads its range "Our Approach to Total Rewards" and the total
label refused it; a range followed by "reflects the salary range" is now
read (Rocket Lab's labelled total still is not). Every description came
through whole (median about 1,100 words, none too short to read).
Unbounce's board is live with no openings that day.

**End to end.** A made-up Toronto profile in a scratch home: `jsa init`
seeded the 22, `jsa discover` read all of them (731 fetched, 93 kept),
matches ranked in km and Canadian dollars, every dashboard page and the
three map tiers answered, job pages showed the role line, `jsa answers`
asked about Canada and named CAD, and `jsa tailor` stopped at the missing
model key, before any call.

**Docker.** `tools/docker_check.sh` builds a Canadian profile in the image
and checks setup, matches with no key, the km dashboard, the postal box and
Canada's map, in CI.

**Lakes up close (2026-11-12).** The fine tier now redraws every Natural
Earth lake within 10 km of a population centre from NRCan's CanVec
1:250,000 waterbodies (Open Government Licence – Canada): 111 of 1,234
lakes, from 457 CanVec pieces, every piece of 1 km² or more, islands under
1 km² drawn as water. The rest keep Natural Earth's outline, as the medium
tier does. Distance from the drawn shore to CanVec's at full detail, every
20 m (median / 95% / worst, metres):

| Lake | Before | After |
|---|---|---|
| Simcoe | 477 / 1606 / 2425 | 6 / 25 / 41 |
| Lac Saint-Jean | 720 / 1829 / 2484 | 6 / 24 / 41 |
| Okanagan | 163 / 515 / 940 | 4 / 21 / 43 |
| Muskoka | 153 / 495 / 888 | 6 / 24 / 41 |
| Great Slave | 238 / 930 / 2260 | 5 / 24 / 41 |
| Winnipeg | 494 / 1433 / 2627 | 7 / 36 / 696 |
| Lake of the Woods | 135 / 7689 / 16447 | 5 / 23 / 42 |
| Rice Lake | not drawn | 6 / 23 / 40 |

Lake Manitoba and Lake Nipigon have no town within 10 km and are unchanged.
Winnipeg's worst figure is not its own shore: Weaver Lake (52 km², no
town within 10 km, so Natural Earth's outline) lies inside the box the
measurement takes. Lake of the Woods first measured 868 m worst: a
boundary-file hole north of Kenora and four slivers, generalized by
Statistics Canada and drawn under the CanVec water. A hole a redrawn lake
covers (a point inside it in a CanVec piece, 80% of its shore within a
mile of that water) is now left out at the fine tier: 21 holes, 5 of them
big enough to be drawn, all in Lake of the Woods. Fine
tier 9.12 MB → 9.75 MB; coarse and medium are byte-identical. Redrawing every
matching lake would have cost about 4 MB, all lakes over 5 km² 13 MB.
Statistics Canada's own lakes file answers automated downloads with a
browser check, and CanVec 1:50,000 is 6.6 GB of waterbodies, so neither
suits a build. CanVec's stated accuracy is 150–250 m; against the
1:50,000 National Hydro Network on Lake Simcoe the median is 15 m.

## Not done

- French page wording (option B; the owner chose A).
