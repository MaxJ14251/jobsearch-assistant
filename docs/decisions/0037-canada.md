# ADR 0037 — Canada, the second country pack

Status: proposed; measurements done, pack not built (2026-10-30)
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

## Decision (to be completed when the pack is built)

Recorded here as the plan for phases 3 to 7; this ADR is accepted when the
pack ships.
