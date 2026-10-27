# ADR 0009 — What the shipped feed list is, and why it is not made bigger

Status: accepted
Date: 2026-09-19
Extends: ADR 0002 (US-only scope).
Relates to: ADR 0008, which named this as the top thing between the repository
and publication.

## Context

`config/companies.yaml` held 54 sources, 50 verified, presented in the README
as "three markets": LA / SoCal (16), San Diego (12), PA / Baltimore (5), plus
AI labs and dev tools (14) and aggregators (3). The first three were the
markets the list was first built for. The obvious reading was that a reader elsewhere
inherits a list built for somebody else, and the obvious fix was to ship more
regions.

Both turned out to be wrong, and the numbers say so.

## What was measured

One discovery pass with `--min 0` stored everything the 50 verified feeds
returned: **9,451 postings**. Four profiles were then scored against that same
stored set, which is exactly what each would have kept, with one pass over the
job boards instead of four.

| profile | kept (≥0.35) | remote | in their state | top 20 |
|---|---|---|---|---|
| Los Angeles, CA | 695 | 247 | 250 | 17 remote, 3 in state |
| Seattle, WA | 728 | 247 | 168 | 17 remote, 3 in state |
| Columbus, OH | 667 | 247 | 5 | 20 remote |
| Boise, ID | 652 | 247 | 0 | 20 remote |

Then, by group — postings each group contributes to each state:

| group | feeds | postings | remote | WA | OH | ID | CA | TX | NY |
|---|---|---|---|---|---|---|---|---|---|
| LA / SoCal | 16 | 4,144 | 169 | 527 | 3 | 1 | 1,513 | 1,022 | 109 |
| AI labs | 14 | 2,950 | 1,145 | 472 | 3 | 0 | 997 | 47 | 725 |
| San Diego | 12 | 1,933 | 60 | 214 | 15 | 0 | 465 | 153 | 94 |
| PA / Baltimore | 5 | 359 | 70 | 3 | 7 | 3 | 8 | 6 | 7 |
| Aggregators | 3 | 65 | 55 | 0 | 0 | 0 | 0 | 0 | 0 |

## What the numbers actually say

**The "regional" blocks are not regional.** SpaceX sits in the LA block and
supplies 473 Washington postings and 1,022 Texas ones. Shield AI sits in the
San Diego block and supplies 170 Washington postings. Seattle's 168 in-state
roles were never configured by anybody; they fall out of large multi-site
employers posting where their offices are. The grouping describes where a
company is headquartered, not where its jobs are, and the README presented it
as the latter.

**The one genuinely regional block is the evidence against building more.**
PA / Baltimore is 5 feeds produced by probing 45 regional employers by hand.
It returned 359 postings — 4% of the corpus — and 7 of them are in Ohio, 3 in
Idaho, 8 in California. It is the most expensive block per posting and the
least useful to anyone, including a reader in Pennsylvania.

**Coverage follows large employers, not configured markets.** That is a
property of which companies publish machine-readable feeds at all, not
something a longer list fixes. Reproducing Seattle's 168 for Columbus would
mean finding and verifying employers in Columbus — and the PA block measured
what that costs and what it returns.

## Decision

**Keep the list. Describe it accurately. Do not grow it regionally.**

1. The README's first screen now says what a reader actually gets, with the
   four measured numbers, instead of leaving it to a "Feed coverage" section
   forty paragraphs down.
2. `jsa doctor` reports the reader's own count — on-site postings in their
   state and remote postings available — as advice, not a failure. Below 25
   local on-site postings it says plainly that this is a remote-roles tool for
   them, and that local coverage means adding employers to
   `config/companies.yaml`.
3. The three location-named groups are kept, because removing them would cost
   every reader the national coverage those employers incidentally provide —
   including the Washington and Texas postings that have nothing to do with
   Los Angeles.
4. A contributed feed is welcome and the path is documented. A shipped
   attempt at national regional coverage is not, until somebody shows a
   cheaper way to find verified feeds than probing 45 employers for one.

## Rejected alternatives

- **Ship a broader default regional list.** The PA block is the measurement:
  high cost per feed, negligible return, and it has to be re-verified whenever
  a board token changes. Rejected on evidence, not on taste.
- **Move the regional blocks into an opt-in example.** Tempting, since 33 of
  50 feeds are location-named and a Columbus reader will never work in those
  metros. Rejected because those same feeds are the largest single source of
  WA, TX, CA and NY coverage; the labels are misleading, the feeds are not.
- **Detect the reader's metro and fetch only nearby employers.** There is no
  employer-to-metro index to do it with, and building one is the previous
  alternative with extra steps.

## Consequences

- A reader outside a big metro is told on day one, by the tool, that remote
  roles are what this holds for them. That is a smaller promise than the
  README used to imply, and it is the true one.
- Discovery still polls all 50 feeds for everyone. A Columbus reader spends
  that time on postings they will not take. Accepted for now: it is one run,
  the feeds are cheap, and per-reader feed selection needs the index that
  does not exist.
- If the feed list ever does grow, this file needs the measurement repeating,
  not an argument.

## Re-measured by kind of employer (2026-09-29, n27)

The README now groups the list by kind of employer rather than by the
regions the list was first built around; the grouping by headquarters had
already been shown above to say nothing about where the jobs are. One
read-only fetch of every feed that answered (50 of them; the nationwide
source is left out because it is asked about the reader's own cities)
returned 9,725 postings:

| kind of employer | feeds | postings | remote | WA | OH | ID | CA | TX | NY |
|---|---|---|---|---|---|---|---|---|---|
| AI labs & dev tools | 14 | 2,986 | 1,156 | 326 | 4 | 0 | 1,163 | 43 | 573 |
| Aerospace, hardware & vehicles | 6 | 4,101 | 64 | 573 | 4 | 2 | 1,440 | 784 | 20 |
| Health & life sciences | 8 | 487 | 73 | 0 | 1 | 1 | 56 | 2 | 4 |
| Software & consumer | 17 | 1,749 | 174 | 93 | 10 | 0 | 461 | 48 | 134 |
| Finance & industrial | 2 | 337 | 0 | 3 | 2 | 0 | 6 | 9 | 7 |
| Remote aggregators | 3 | 65 | 52 | 0 | 0 | 0 | 0 | 0 | 0 |

The finding stands: large multi-site employers carry Washington and Texas
(573 and 784 on-site postings from aerospace, hardware and vehicles alone),
while Ohio and Idaho get almost nothing from any group.

## New board types, 2026-10-07 (plan 28)

Recruitee boards can now be read (`kind: recruitee`). That widens what a
reader can add to their own list; it adds no employer to the shipped one.
The shipped list still grows only with a measurement, as above. Three
Recruitee boards were checked with `jsa verify` as a local trial (7, 26 and
76 postings) and were not committed.

## Four employers added after a measurement, 2026-10-26 (G9)

The first growth of the shipped list since this decision. Candidates came
from one owner's tracker, read-only: employers seen through The Muse, USAJOBS
or the remote RSS feeds with no board of their own configured (52 of them).
Federal agencies (USAJOBS covers them), staffing firms and talent
marketplaces, and YC-hosted pages were left out. Of the 20 most frequent, the
owner chose eight to try. Each board was found with `jsa find` (Greenhouse
names the board; Ashby does not) or, for Workday, which cannot be guessed, by
one list request to a known tenant URL. The owner confirmed every board was
the employer's own before anything was measured.

Each board was then fetched once and scored in memory against that profile,
the way discovery does, writing nothing:

| employer | kind | postings | kept (≥ 0.35) | ≥ 0.5 | in the owner's cities | remote | seconds |
|---|---|---|---|---|---|---|---|
| Disney | workday | 371 | 24 | 18 | 17 | 0 | 136 |
| Flexport | greenhouse | 188 | 19 | 5 | 7 | 0 | 1 |
| Celonis | greenhouse | 224 | 16 | 13 | 0 | 5 | 2 |
| Reddit | greenhouse | 161 | 15 | 8 | 0 | 15 | 2 |
| Bank of America, searched "software engineer" | workday | 102 | 9 | 7 | 0 | 0 | 35 |
| Bank of America, unsearched | workday | 247 | 1 | 1 | 0 | 0 | 162 |
| Modern Animal | greenhouse | 101 | 4 | 0 | 4 | 0 | 1 |
| Bild AI | ashby | 4 | 2 | 1 | 0 | 0 | 1 |
| Sunrun | workday | 222 | 2 | 0 | 1 | 0 | 194 |

Added: Disney, Flexport, Celonis and Reddit. Why each is worth shipping to
other readers, not only this one:

- **Disney** is a large multi-site employer, the kind this decision found
  carries in-state roles nobody configured: its board's first postings were
  technology roles in Florida, while 17 of the 24 kept were in the owner's cities.
- **Flexport** posts outside this owner's area too (4 kept postings were
  over 50 miles away) and costs one request.
- **Celonis** and **Reddit** post remote US roles, which is what this list
  holds for a reader outside a big metro (above). One request each.

Not added: Bank of America (no posting in the owner's cities or remote,
even narrowed), Sunrun (over three minutes a run for two weak matches,
mostly field sales), Modern Animal and Bild AI (a few weak matches; a reader
can add them to their own list).

Verified with `jsa verify` (56 of 60 sources answered; all four new ones
did) and one real `jsa discover` run afterwards: Disney 24 new postings,
Flexport 19, Celonis 16, Reddit 15, so 74 of the run's 99 new listings came
from the four. The run took 17 min 56 s; the previous daily run's discovery
took about 15 min 40 s, so the four cost about 2¼ minutes, almost all of it
Disney's detail requests.
