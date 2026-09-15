# ADR 0003 — Application lifecycle and the approval surface

Status: accepted
Date: 2026-09-15
Decided by: /software-design-council, chaired during the "application lifecycle
and approval CLI" goal.

## Context

`jsa/approvals.py` has been complete and tested since Goal 03 — `queue()`,
`pending()`, `approve()`, `reject()`, `is_approved()`, `record_event()`, 19
tests. The four commands Goal 03 specified were never built, so approval was
reachable only through the web UI, and **no code path created an
`applications` row at all**. The tracker — deliverable 2 of the original brief
— had never held a single application.

### What the evidence already settled

Two of the five questions turned out to be answered in the code, and the
answers are better than the guesses:

- **The web UI does not duplicate the approval logic.** `web.py::do_approve`
  calls `approvals.approve(con, approval_id)` and `do_reject` calls
  `approvals.reject(...)`. There is already exactly one path to
  `decided_by='human'` — `approvals._decide()`. The CLI joins that path rather
  than creating a second one.
- **`applications.job_id` is `NOT NULL UNIQUE`.** One application per job is
  a database constraint, not a convention. Saving the same job twice cannot
  produce a duplicate.

Also relevant: `_decide()` already refuses to re-decide an approval that is
not pending, and `reject()` already refuses empty feedback. The library was
built for this; only the surface was missing.

## Decisions

### 1. An application is created explicitly, by `jsa save`

Not implicitly on first tailor.

The status vocabulary in the schema already models this — `saved`
("discovered and kept") precedes `drafting` precedes `ready`. Auto-creating
an application whenever someone drafts a speculative resume would fill
`v_pipeline` with roles that were never pursued, and the pipeline view is the
tracker's whole point.

**Consequence, accepted:** `jsa tailor` will refuse to run on a job with no
application and tell the reader to run `jsa save <job_id>` first. That is one
extra command. The alternative — a tracker whose contents mean "I once ran a
command" rather than "I am pursuing this" — is worse.

### 2. `documents.job_id` is authoritative. `applications.resume_doc_id` is a cache

The schema already says so, in a comment above the column: *"Denormalized for
fast dashboard sorting; kept in sync by the app layer."*

That phrase is also a warning. "Kept in sync by the app layer" is how two
copies of a fact drift apart, and this project has already paid for that twice
(the seniority vocabulary versus the schema CHECK; three rival copies of the
personal-data scan).

So:

- The **document** is the record. `documents.job_id` is the real link.
- `applications.resume_doc_id` / `cover_doc_id` point at the **current**
  document of that kind, for dashboard sorting. They are derived.
- Exactly one function may set them, and a test asserts the invariant: when
  `applications.resume_doc_id` is set, that document's `job_id` must equal the
  application's `job_id`. A pointer that crosses jobs is corruption, not a
  quirk.

**Can a job have documents with no application?** The schema permits it —
`documents.job_id` references `jobs`, not `applications`. In practice decision
1 prevents it, because tailoring requires a saved application. The permissive
schema is left alone: tightening it would buy nothing and break the ability to
keep a document whose application was withdrawn.

### 3. A tailored resume is approved as `subject_type='document'`

Not `'application'`. Approval is **per document version**: re-rendering after
a rejection creates a new document and a new pending approval, and never
inherits the old decision. `_decide()` already enforces the second half by
refusing to re-decide a settled approval.

`idx_approvals_one_pending` is `(subject_type, subject_id) WHERE decision =
'pending'`, so one pending decision per document falls out of the index.

**`subject_type='application'` is deliberately unused.** It remains in the
CHECK constraint for a future "approve this application as a whole" step if
one is ever wanted. A later session should not assume it means something
today.

### 4. `jsa applied` records reality, and does not require an approval

`applications.status='applied'`, `applications.applied_at`, plus an
`application_events` row with `actor='human'`.

It does **not** refuse when no approved document exists. This looks like a
hole in the approval gate and is not, because the two things protect different
parties:

> The approval gate exists to stop **the agent** acting autonomously. It does
> not exist to stop **the human** doing what they choose.

A person may legitimately apply with a resume this tool never generated, or
one they edited by hand afterwards. The tracker's job is to be accurate about
what happened. Refusing to record a fact the human is reporting would make the
tracker wrong, and a tracker that argues with reality gets abandoned.

It **warns** when no approved document exists, and the event note records that
the submission had no approved document behind it — so the trail stays honest
without becoming an obstacle.

`jsa applied` on a job with no application row is an error, not a silent
create. Reporting a submission for something never saved means one of the two
is a mistake, and guessing which would be wrong.

### 5. One path to `decided_by='human'`, locked mechanically

Already true; now guarded. A test asserts that `approvals.py` is the only
module in `jsa/` containing the string `decided_by`, so a future contributor
adding a convenient shortcut breaks the build rather than the guarantee.

## Consequences

- The tracker holds real applications for the first time.
- Both surfaces (CLI and web) reach a decision through `approvals._decide()`.
- `jsa tailor`, when built, must call `require_application()` and must set the
  denormalized document pointers through the single function named in
  decision 2.
