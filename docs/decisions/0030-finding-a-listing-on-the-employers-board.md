# ADR 0030 — Finding an aggregator listing on the employer's own board

Status: accepted
Date: 2026-10-05
Relates to: ADR 0013 and ADR 0020 (sources the tool may not read), and the
project's founding rule that nothing sends automatically. Implements plan 21
of the planning session.

## Context

Many LinkedIn and Indeed listings don't link through to the employer. The
owner asked for a tool that applies to them automatically. That was declined
on 2026-10-04:

- LinkedIn's and Indeed's terms forbid automation, and the project already
  refuses both.
- An Easy Apply bot would put the owner's account at risk and would have to
  evade bot detection.
- "Nothing sends automatically" is the project's founding rule.

The owner chose two plans instead: this one, which looks for the same job
where the tool **is** allowed to look, and plan 22, a checklist for applying
by hand.

## Decision

`jsa find "<company>" "<title>" [--city C] [--link URL]`, and a "Seen it on
LinkedIn or Indeed?" form on the dashboard's Add page.

1. **Typed facts only.** The operator types the company, title and city the
   listing shows. A link may be given; it is kept as text and never
   requested. A link typed into the company, title or city field is refused
   with what to type instead.
2. **A host allowlist.** Every request `jsa/find.py` makes is checked
   against the boards' public API hosts (`boards-api.greenhouse.io`,
   `api.lever.co`, `api.ashbyhq.com`, `apply.workable.com` and a
   `<tenant>.wdN.myworkdayjobs.com` API). Any other host raises
   `HostNotAllowed`: a bug, never a fallback. A test asserts every mocked
   request's host.
3. **Company names are matched, never picked.** Typed names often miss their
   own rows: "Perplexity AI" is `perplexity` in companies.yaml, and "Mitek
   Systems", "Utz Brands" and "Highmark Health" had the same problem. So
   matching tries the exact slug, then the slug without trailing words like
   `ai`, `systems` or `inc` (a named list), then a word prefix. Every
   candidate is searched and listed with why it matched; when there are
   several, the operator is told to check which one is the employer.
4. **Three answers.**
   - **Same job** means the same normalized title (place qualifiers folded,
     as `scoring.dedup_key` does) and the same city. It comes only from the
     tracker or a configured board. This is the `db.find_duplicate` rule: a
     wrong merge hides a job.
   - **Possible** covers a title sharing at least 60% of its words
     (`TITLE_OVERLAP`, Jaccard), another city, no city given, or anything
     from a guessed board.
   - **Not found** means apply on the listing site.
5. **A configured board is checked live**, once, to catch postings newer
   than the last discovery. Workday is searched with the typed title
   server-side, so its detail requests stay few. A posting found that way
   that isn't stored yet offers `jsa add <link>` (the existing path).
6. **Guessed boards are held to "possible".** They are tried only for an
   employer with no configured board: at most 3 token guesses across
   Greenhouse, Lever and Ashby, and at most 9 requests in all, at the
   fetchers' polite pace.
   - **Greenhouse** names its board (`/v1/boards/{token}`). The name must
     match the typed company before its jobs are read, so someone else's
     board is never looked at.
   - **Lever and Ashby** give no name. A hit there is labelled "a board
     named <token> exists; confirm it is this employer".
   - **A board with no postings is not suggested.** On 2026-10-05, Ashby
     answered 200 with an empty list for a token the employer doesn't use,
     so an empty board is no evidence.
   - **A guessed board can never say "same job".**
7. **companies.yaml is never written.** A guessed board that answers prints
   a ready-to-paste entry with `verified: false`, and points to `jsa verify`
   (CONTRIBUTING's path).
8. It reads only, and stores nothing. "Add this job" is the existing
   `/add/link` route, pressed by the operator.

## Fixed on the way

Planning this found two small bugs, fixed in their own commit:

- `intake.add_pasted` didn't mark pasted text as pasted, so discovery could
  overwrite it.
- `db.upsert_company` reset a yaml employer's priority whenever an
  aggregator named it.

## Measured

2026-10-05, read-only, on the real tracker and the live public boards:

| Case | Answer | Time |
|---|---|---|
| An employer with a configured Greenhouse board, its typed name carrying an extra "Inc" | same job, with its tracker number | 2.0 s |
| An employer with a configured Ashby board, typed with an extra "Labs" and a shorter title | 2 possible | 2.2 s |
| An employer with no configured board | 1 possible from a guessed Greenhouse board named after it; an empty guessed Ashby board was reported, then dropped by the rule above | |

The owner's own check, three listings seen on LinkedIn, is still to come.

## Consequences

- An operator who sees a job on LinkedIn finds out in seconds whether to
  apply on the employer's own site instead, without the tool ever touching
  LinkedIn.
- Most "possible" answers need a human look; that is the price of never
  merging on a guess.
- Workday tenants are not guessed. The tenant, the `wd` number and the site
  can't be derived from a name.
