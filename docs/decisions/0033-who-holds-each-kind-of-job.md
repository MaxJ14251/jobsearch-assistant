# ADR 0033 — Who holds each kind of job, nationwide

Status: accepted
Date: 2026-10-08
Relates to: ADR 0032 (what postings ask for), ADR 0019 (shipped public data,
the build-script pattern). Implements plan 33.

## Context

"Do people get this job without a bachelor's?" has a public answer by
**occupation**, not by company. ADR 0032 records why company-level hiring
data doesn't exist publicly.

## Sources, checked 2026-10-08

- **BLS Employment Projections, table 5.3**: "Educational attainment for
  workers 25 years and older by detailed occupation, 2023–24 (Percent)",
  https://www.bls.gov/emp/tables/educational-attainment.htm (last modified
  September 1, 2026). "Data are from the 2023 and 2024 American Community
  Survey Public Use Microdata." Seven levels from less than high school to
  doctoral or professional degree, 832 rows with the all-occupations total,
  on the 2025 National Employment Matrix codes. It is what workers **hold**;
  BLS's table 5.4 is what entry typically requires. Public US-government
  data.
  - **BLS answers scripts with 403**, even with an identifying User-Agent and
    ordinary browser headers. Nothing tries to get around that: the build
    reads the table from a file a maintainer saves from the published page
    (`tools/build_role_data.py`'s docstring has the one-line browser console
    snippet). The shipped rows were read from the page in a browser on
    2026-10-08.
- **O*NET 31.0 Database** (USDOL/ETA), https://www.onetcenter.org/database.html,
  under the "O*NET® 31.0 Database Content License", CC BY 4.0. Attribution
  required: the database, USDOL/ETA, a link to the license, and, for a
  modified version, "has modified all or some of this information" and
  "USDOL/ETA has not approved, endorsed, or tested these modifications".
  Used: Occupation Data (1,016 rows), Sample of Reported Titles (8,189), Job
  Titles (54,269). O*NET downloads work from a script.

## Decision

1. **`tools/build_role_data.py`** writes two files into
   `jsa/resources/data/`, shipped like the map data and read with no network:
   - `role_education.csv.gz`: 831 occupations, the seven shares and the
     survey years;
   - `role_titles.csv.gz`: 42,772 normalized titles mapped to 819 of those
     occupations. A title shared by several occupations goes to the one with
     the most weight: the occupation's own title counts 10, a title workers
     reported 3 (5 when O*NET's career site shows it there), a lay title one
     per source that reported it. A short override list fixes common titles
     O*NET files under occupations whose people mostly do other work:
     account executive, account manager and customer success manager to
     sales representatives of services; site reliability engineer to
     computer occupations, all other. Both files are deterministic for the
     same inputs.
2. **`jsa/roles.py`**: a posting's title is normalized (seniority, level,
   place and parenthetical words dropped; singular; common short forms
   expanded) and matched: word for word (confidence 1.0); else the longest
   known title of two or more words it ends with ("Strategic Account
   Executive" → account executive, 0.85); else on shared words, only with
   the same last word and at least 0.7 of the words shared. Otherwise
   **nothing is shown**.
3. **The line**, on the job page and the Turbo card: "This role, nationwide:
   Software developers (15-1252): 3% high school or less · 11% some college or
   associate's · 52% bachelor's · 34% graduate degree. People in this role
   nationwide, not this company's hires." The occupation and its code are
   always named, so a poor match is visible. A Sources page (`/about-data`)
   carries the BLS citation and O*NET's required attribution, linked from
   every line.

## Measured on the owner's tracker, 2026-10-08

1,480 distinct open titles:

| version | matched | sampled right |
|---|---|---|
| first: exact or 0.6 word overlap | 73% | 26 of 30 |
| singular titles, source-count weights, 0.7 overlap | 56% | 25 of 30 |
| plus overrides and the ending rule (shipped) | **74%** | **27 of 30** |

The three misses in the last sample are O*NET's own filing of two-word
titles ("reliability engineer" under logisticians, "development engineer"
under chemical engineers, "evaluation engineer" under electronics
engineers). The plan's spot check of 30 by the owner is still owed.

## Consequences

- BLS publishes a new table yearly; rebuilding means saving the page again.
- An occupation is a wide category: "software developers" covers a new
  graduate and a principal engineer alike.
- Nothing here says who a company hires (ADR 0032).

## Rejected

- **O*NET's web API at runtime.** It needs registration and a network; the
  shipped files work offline.
- **Only the curated O*NET titles.** Without the lay titles, 59% of titles
  matched and the misleading filings were still there.
