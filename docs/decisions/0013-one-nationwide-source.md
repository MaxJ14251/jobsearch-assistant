# ADR 0013 — One nationwide source, not fifty state lists

Status: accepted
Date: 2026-09-26
Amends: ADR 0009 (what the shipped feed list is).

## Context

Every feed this project shipped was one employer's own job board. The
coverage of the tool was therefore the coverage of those employers' offices,
and the author's employers are concentrated in a few metros.

Measured on 2026-09-26, over the 1,147 open postings the 50 shipped feeds had
collected:

| | |
|---|---|
| States with at least one on-site posting | 26 of 51 (incl. DC) |
| States with none at all | 25 |
| 1–4 postings | AZ CT HI MA MS NC NE NM NV OH UT WY |
| Concentration | CA 364, NY 131, WA 109, TX 94, DC 53 |
| Remote, available to anyone | 414 |

ADR 0009 decided not to grow the list region by region: a hand-maintained
list of employers rots silently, and verifying each board token is work that
falls on one person. That decision holds. It named the alternative without
taking it — a source that is not a single employer.

## Decision

**Ship one aggregating source, queried with the operator's own cities.**

1. **The Muse's public API** (`kind: themuse` in `config/companies.yaml`).
   Chosen over the alternatives probed the same day:
   - *USAJOBS*: federal, all fifty states, public-domain data, and no
     restrictive terms — but federal only, and its search API returned 401
     without a key, so it could not be measured. Worth adding as a second
     source; not a reason to delay this one.
   - *Arbeitnow*: free and keyless, but 1 of 325 postings looked American.
   - *Remotive, RemoteOK*: remote only, which is the part already covered.
   - *LinkedIn, Indeed, Glassdoor*: refused. Their terms forbid this, and
     `jsa add --paste` already covers a posting somebody found themselves.
2. **The query is the operator's `locations`, not a board token.** Discovery
   passes the profile's own cities in; an entry with no locations is a
   configuration error that says so.
3. **A key is required by their terms** (registration for anything beyond
   testing), so `MUSE_API_KEY` lives in `.env`. Without it the source is
   reported as *skipped*, the rest of discovery runs, and `jsa doctor` says
   where to get one and what it would change.
4. **Bounded, not mirrored.** Five pages per city per run, ~100 postings.
   Their terms forbid cloning the content (3.3g) and ask that displayed
   postings link back (3.4) — the stored URL is their posting page, so a
   person who clicks through lands where the terms require.

### What it delivers

Measured 2026-09-26, one run of 100 postings per city, after parsing:

| City | In-state on-site | Remote | Distinct employers |
|---|---|---|---|
| Boise, ID | 9 | 91 | 17 |
| Columbus, OH | 57 | 43 | 21 |
| Seattle, WA | 73 | 38 | 18 |
| Los Angeles, CA | 79 | 30 | 21 |

Against the n9 baseline from the employer feeds — 250 in-state for Los
Angeles, 168 for Seattle, 5 for Columbus, **0 for Boise** — the change for
somebody outside a tech metro is the whole point. Boise goes from nothing to
a steady trickle from seventeen employers a run.

## Recognising a posting that is already here

An aggregator lists jobs the tracker already holds from the employer's own
board, under a different posting id and a different URL, so the
`(source, external_id)` key cannot see it. `db.find_duplicate` matches on
**same company, same normalised title, same first location**, across sources.

It errs deliberately toward showing a posting twice:

- The company is resolved to one row by slug first, so two employers with
  similar names cannot collide.
- The same title in two cities stays two jobs. That is what the tracker's
  existing `dedup_key` grouping is for, and merging them would hide one.
- When either side has no location, it reports **no** duplicate.

Rationale: a duplicate row is visible and annoying; a wrong merge hides a job
the operator would have applied to, and they never learn it existed.

When a duplicate is found, the **employer's own row wins** — it came from the
employer, and its URL is where you actually apply. The aggregator's copy is
dropped, not merged.

## Consequences

- `sources.kind` gains `'themuse'`. SQLite cannot alter a CHECK, so
  `_rebuild_sources_if_stale` rebuilds the table on an existing database, the
  same way the approvals table was widened for ADR 0011.
- Discovery's report gains a `duplicate` count, so the overlap between this
  source and the employer boards is visible rather than assumed.
- ADR 0009's "the list is not simply made bigger" is unchanged for employer
  boards. This is one entry, not fifty, and it is the only one whose query
  depends on the operator's profile.
- If The Muse withdraws the API or changes its terms, the tool degrades to
  what it was: remote roles everywhere, local roles where the shipped
  employers have offices. That is a real dependency, and the reason USAJOBS
  is recorded here as the obvious second source rather than a discarded one.

## USAJOBS added, 2026-10-07 (plan 29)

USAJOBS is now the second source asked about the operator's own cities
(`kind: usajobs`). The 401 recorded above was its key requirement: a free key
from developer.usajobs.gov, sent with the requesting email as the
User-Agent. Its API terms (section 2, read 2026-10-07) allow this use: the
data is "for the explicit use of the requesting company or individual", may
be stored and reformatted "for internal application purposes", must keep
"displayed data values" unaltered, credit USAJOBS and send people to USAJOBS
to view and apply, and may not be redistributed. So every person uses their
own key; postings keep USAJOBS's own text and link to their USAJOBS page,
and the job page says the posting is from USAJOBS.

It is shaped like The Muse: per city, bounded (`USAJOBS_MAX_PAGES = 2`
pages of 100), skipped without settings, one row across cities, and the
agency as the employer, so `find_duplicate` works as before.

### Measured, 2026-10-09

One run with the owner's key over the owner's 15 cities (counts only; the
cities are not named here):

- **663 distinct postings, 73 agencies.** One city returned none. The others
  matched 63 to 636 postings each; 9 matched more than the 200 the bound
  reads. Neighbouring cities return almost the same set, so 15 cities read
  663 postings, not 15 × 200.
- **Per city, of what was read:** 32–34 remote (the same nationwide remote
  postings in every city), 24–78 telework-eligible, 7–142 on-site.
- **5 of 663 pass `min_score`** (1–4 per city). `jsa discover` the same day
  kept 5 and filtered 658. Federal titles rarely use the words in a tech or
  sales profile's target titles.
- **`Keyword` from the first 3 target titles:** 5 postings matched in all
  15 cities together; 1 passed `min_score`, and it was not in the plain
  read. One more keeper for 45 more requests, and a search that would hide
  every federal posting worded differently from the profile. Not used.
- **The page bound stays at 2.** Reading every page of the 9 capped cities
  would roughly double the requests for postings that pass at under 1%.

For this profile USAJOBS is a small source: a handful of keepers a run.
For a profile whose roles federal agencies hire for (analysts, IT
specialists, technicians), it would be a large one, which is why it stays
shipped and verified. Without a key it is skipped, like The Muse.
