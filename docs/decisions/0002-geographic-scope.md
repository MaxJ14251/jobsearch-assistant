# ADR 0002 — Geographic scope: United States only, for now

Status: accepted
Date: 2026-09-15

## Context

This project is being published for general use. That raises a question the
code already answers implicitly, in a regex, where nobody will find it:
**which countries does this tool serve?**

`jsa/scoring.py` hard-rejects non-US locations. `_US_RE` and `_NON_US_RE` feed
`is_non_us()`, which is called from two places in `score_job` and returns a
score of 0 with a reason. A job in Berlin does not rank low — it is excluded.

This was originally a personal filter for one operator in Los Angeles and
south-central Pennsylvania. It is now a product decision, and it should be
stated rather than discovered.

## Decision

**United States only, deliberately, for now.** Not as an oversight, and not as
a claim that other countries do not matter.

The currency assumption follows from it. `compensation_floor_usd` names USD in
the field itself, which is the right call — an unmarked `compensation_floor`
would silently mean different things to different readers.

## Why not internationalise now

Going international is not a translation problem, it is several problems, and
none of them are started:

- **Salary**: currency symbols, conversion, and whether a floor is even
  comparable across markets. See ADR 0001 — salary is not extracted at all
  yet, in any currency.
- **Work authorization**: the single most country-specific field in the
  profile. "US citizen" has no meaning in an EU context; right-to-work rules
  differ per country and per visa class.
- **Location matching**: `_US_RE` matches state abbreviations and US city
  patterns. `_matches_city()` requires city AND state, which is a US postal
  concept — after a town named York matched "New York, NY".
- **Feeds**: the 50 verified sources are US company boards.

Doing any one of these badly is worse than not doing it. A location filter
that half-works discards jobs invisibly, which is the failure mode this
project keeps guarding against.

## What a future contributor should NOT do

Do not add currency parsing, EUR handling, or non-US location patterns as a
side effect of some other change. That is the specific thing this ADR exists
to prevent. Internationalisation is its own piece of work with its own ADR,
and it starts with deciding what "work authorization" means when it is no
longer a single country's concept.

## Consequences

- `is_non_us()` stays a hard reject and is correct, not a bug to be fixed.
- The README should say the tool is US-focused, so a stranger in Manchester
  learns it in the first minute rather than after configuring a profile.
- Nothing about the profile schema blocks internationalisation later. The
  fields that would need to change — `compensation_floor_usd`,
  `work_authorization` — are already named or shaped so that a future change
  is additive rather than a rewrite.
