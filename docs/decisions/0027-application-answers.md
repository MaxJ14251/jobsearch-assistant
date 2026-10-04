# ADR 0027 — Application answers

Status: accepted
Date: 2026-10-04
Relates to: ADR 0001 (compensation), ADR 0007 (cover letters fail open), ADR
0010 (outreach verification). Implements plan 17 of the planning session.

## Context

After Turbo and Review, the slowest step left in applying is typing answers
into each employer's form. Most of them are facts the profile already holds.

## Decision

1. **Fact answers come from the profile, verbatim, with no model** (`jsa
   answers`, and the job page with a Copy button each): work authorization,
   sponsorship, relocation, work arrangement, links, the education line
   (credential verbatim), years of experience (summed from the person's own
   dates, overlaps once, labelled as a computation). An undecided value says
   "Not decided in your profile" and names the field. They are read live and
   never stored, so they never go stale.
2. **No salary answer.** The floor is a threshold, not an asking figure, and
   never leaves the profile (ADR 0001); the answer shows the posting's stated
   range or says it states none. The floor-leak test now covers `answers.py`,
   `facts.py` and `letter.py`.
3. **Written answers fail open, with a note** (ADR 0007). "Why this role"
   and "a relevant project" are drafted in one model call over the person's
   own bullets (the latest approved resume's, else selection's), then held
   to the cover letter's checks: unsupported words, unfinished work written
   as finished, do-not-claim terms, `assert_no_degree_claim`, 40–150 words.
   A refused one gets one retry that names the refused words; one that
   still fails is built from the person's summary and top bullet, labelled
   "from your own sentences". Stored in `application_answers`; a regenerate
   archives the previous set.
4. **"Why this company" is composed only.** Measured on 5 of the owner's
   saved jobs (2026-10-04), with the retry: why_company passed 0 of 5. The
   plan's rule was composed-only under half (ADR 0010 had measured 1 of 9
   for sentences about an employer). The other two passed: why_role 0 of 5,
   relevant_project 2 of 5; every refusal was for words the profile doesn't
   use (some real additions such as "observability", "infrastructure",
   "autonomous"; some harmless, such as "exactly", "includes"). They stay
   drafted because they fail open; revisit if the composed versions are the
   ones the owner keeps using.
5. **No approval, nothing submitted.** Answers are copied by hand, like
   prep. `answers.py` imports no sending or network module (tested).

## Fixed on the way

`prep.degree_answer` said "did not finish the degree" and "the
certifications in 2026" whatever the profile held, and `gap_answer` named
one fixed date. Both are now built from the profile (`jsa/facts.py`): a
completed degree is stated as written, and no gap means no gap drill.
