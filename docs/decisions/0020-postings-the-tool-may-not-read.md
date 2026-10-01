# ADR 0020 — Postings the tool may not read

Status: accepted
Date: 2026-09-30
Relates to: ADR 0005 section 11 (the thin-posting warning), ADR 0006 (pay),
ADR 0013 (one nationwide source). Implements plan 2 of the 2026-09-30
planning session.

## Context

Some feeds deliver a link, not a posting. The thin-posting warning (ADR 0005
section 11) made that visible: 53 of 1,626 open postings have under 40 words
of description. Measured 2026-09-30:

- **Hacker News** items are stubs: an article URL, a comments URL and a
  score. We Work Remotely and Python.org carry full text (median 5,082 and
  2,075 characters); Snap's API carries none; Gopuff's are one line.
- **None of the 20 live Hacker News jobs links to a board `jsa add` can
  read** (Greenhouse, Lever, Ashby, Workday): 8 go to Y Combinator job
  pages, 11 to company sites, 1 to the discussion thread.
- **Y Combinator's pages may not be read automatically.** Their robots.txt
  allows the job pages, and each carries a structured `JobPosting` record,
  but YC's terms of use (ycombinator.com/legal, read 2026-09-30) rule out
  "data mining, robots, scraping or similar data gathering or extraction
  methods". That puts YC with LinkedIn and Indeed: refused.
- **Company sites are not fetched by rule** (`jsa/intake.py`): a scraper for
  arbitrary career pages would be wrong often enough to invent facts.

So the only route to the real text is the operator, by hand. The existing
route was wrong for this: `jsa add --paste` created a **second** job, leaving
the application, the drafts and the rejection notes on the stub; and the next
`jsa discover` rewrote any description from the feed, stub included.

## Decision

**The operator fills in the posting in place, and discovery never
overwrites it.**

1. `jobs.description_origin`: NULL from a feed, `'pasted'` when the operator
   supplied the text. `migrate()` adds it to existing trackers.
2. `jsa fill <job#>` (and a form on the job page, shown only when the
   posting is thin) replaces the description of the **same** job. Its
   number, application, drafts and history stay. Pay is re-read from the
   text unless the board's own pay field set it (ADR 0006); score and track
   are recomputed; enrichment is cleared so it reads the new text. A paste
   under 200 characters is refused, as for `jsa add --paste`. If the job has
   an application, an event records "posting text pasted by hand", at the
   same stage, by the human who ran it.
3. `db.upsert_job` leaves a pasted row alone on rediscovery, except to clear
   `closed_at`: the listing is still live, and the operator's text outranks
   the feed's stub.
4. The thin-posting warning names `jsa fill <job#>` instead of `jsa add`.

## Rejected

- **Fetching YC or company pages.** Refused above, for the same reasons as
  LinkedIn and Indeed.
- **Dropping the Hacker News source.** It finds startup roles no employer
  feed lists. With the warning and this route, a stub becomes usable in one
  paste. Revisit if filling feels like too much work.

## Consequences

- A filled job is the operator's text from then on. If the employer edits
  the posting, the tracker keeps the pasted version; fill it again to update.
- Snap's empty listings and Gopuff's one-liners can be filled the same way.
  No source change is planned for them.
- Interview prep and outreach read the filled text like any other.

## Location, added the same day

The first real fill showed the gap: Kyber's stub was stored as remote with
no location, the fill left both alone, and the score said "remote is
acceptable" for a job that is on-site in New York. A fill now takes the
location too (`jsa fill <#> --location`, or the field above the paste box,
pre-filled with what the stub has) and works out remote, hybrid or on-site
from it, as discovery does. Without one, the stub's is kept. Kyber's score
went from 0.89, on the stub's "remote", to 0.66 on its real location.
