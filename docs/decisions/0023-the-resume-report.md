# ADR 0023 — The resume report

Status: accepted
Date: 2026-10-02
Relates to: ADR 0005 (the profile is the operator's own description), ADR
0011 (feedback triggers nothing). Implements plan 11 part 3.

## Context

The tool may never write a claim: every sentence on a resume traces to the
profile. So when a resume is weak because the profile is thin (a job with
one bullet, no figures anywhere, a gap with nothing in it), only the person
can fix it, and nothing told them what to fix.

## Decision

`jsa/coach.py` reviews the profile, and the draft when there is one, and
reports what could be added and where. It is printed at the end of `jsa
tailor`, on its own as `jsa coach`, stored with each drafted resume
(`documents.coach_findings`, JSON), and shown on the review page beside
Approve.

1. **It never blocks.** Approval is the person's decision; a rule that
   blocked it would be the tool deciding (the same rule as "suggest, never
   decide" for stages, ADR 0021). The review page shows the report next to
   Approve so it is seen at the moment of deciding. `jsa coach --strict`
   exits 1 on any finding, for anyone who wants a gate in their own workflow.
2. **No model.** Pure functions over the profile and the draft; `coach.py`
   imports nothing from `jsa.llm`, and a test checks it.
3. **It says what is missing, never what to say.** Messages name the gap and
   the profile path to edit, with "if you have one" / "only if true".
4. **Field-agnostic.** Word lists (soft traits, filler phrases,
   in-progress leads, acronyms) live in `config/coach.yaml`; any rule can be
   turned off with `coach.disabled` in the profile, and the gap threshold
   set with `coach.gap_months`.

Rules: `thin_entry` (a job shown with fewer than 2 bullets), `no_numbers`
(more than half of an entry's bullets have no figure; a count, not a flag
per bullet), `unexplained_gap` (over 6 months with no job, dated project or
certification), `in_dev_only` (every bullet of a project is in progress),
`soft_skill`, `vague_summary`, `opaque_cert` (a name that reads as a code,
with no `display_name` or `description`), `missing_skill_evidence` (a
project `stack` item, or an `ats_keywords.have` term the bullets use, that
skills don't list). The last was narrowed from "any capitalized tool name in
a bullet", which would fire on employers, places and sentence starts.

The two thresholds (6 months, half the bullets) are defaults, not
measurements; the code says so.

## Consequences

- On the owner's profile, `jsa coach` reported 9 findings on its first run
  (2 no_numbers, 1 in_dev_only, 1 soft_skill, 1 opaque_cert, 4
  missing_skill_evidence). Fixing them is editing `master_profile.yaml`, which
  the owner does.
- A finding on the review page is advice about the profile, not about the
  draft: rejecting a draft for it changes nothing until the profile changes.
