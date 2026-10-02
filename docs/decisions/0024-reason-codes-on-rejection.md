# ADR 0024 — Reason codes on a rejection

Status: accepted
Date: 2026-10-02
Relates to: ADR 0003 (application lifecycle), ADR 0011 (what rejection
feedback is for), ADR 0023 (the resume report). Implements plan 11 part 6.

## Context

Every rejection carries free text, and ADR 0011 decided that text is read by
a person and triggers nothing: "Twelve notes is not a dataset." Free text
can't be counted later without classifying it, and classifying it
automatically is exactly what ADR 0011 rejected.

## Decision

1. **An optional code, picked by the reviewer**, beside the required text:
   `fabrication`, `wrong_bullets`, `missing_section`, `formatting`,
   `weak_content`, `wrong_summary`. On `jsa reject --reason` and as a
   dropdown on the review page. Nothing assigns a code automatically.
2. **Stored in `approvals.reason`**, a nullable column added by
   `db.migrate()`. It is validated in `approvals.reject`, not by a CHECK:
   changing a CHECK forces a table rebuild (`_rebuild_approvals_if_stale`).
   `jsa tailor` and `jsa reject` upgrade the tracker first.
3. **Never in a prompt.** `tests/test_reject_reasons.py` checks that no module
   puts a code into a prompt and that the prompt-building modules never read
   one, as `tests/test_feedback.py` does for the free text.
4. **One use now: the redraft hint.** When version N-1 was rejected as
   `wrong_bullets`, `jsa tailor` prints that version's bullet ids beside the
   new ones, for the person to compare. The selection itself doesn't change.
5. **The resume report is stored per document** (`documents.coach_findings`,
   ADR 0023), as the full findings rather than only their ids, so the review
   page can show them as they were at draft time.

## Deferred

`jsa feedback-report`, which would set codes against report findings, waits
until there are **at least 30 coded rejections**. Below that, a correlation
is noise, and ADR 0011's objection stands. Revisit at 30.
