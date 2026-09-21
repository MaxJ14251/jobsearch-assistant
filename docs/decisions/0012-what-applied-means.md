# ADR 0012 — What "applied" means

Status: accepted
Date: 2026-09-21
Relates to: ADR 0003 (application lifecycle), ADR 0011 (superseding).

## Context

`jsa applied` had never been run against a real application. Checked before
the first one, it recorded a date and a status and nothing about what was sent.
The only link from an application to a document was
`applications.resume_doc_id` / `cover_doc_id`, and that had three problems:

1. **It means "latest drafted".** `set_document_pointer` runs on every draft.
   Shown in a sandbox before any change:
   `sent doc 1; after a redraft the application says 2: MOVED`. Every resume
   pointer in the author's tracker happened to be correct. That was luck.
2. **"Approved" meant "any document for this job is approved".** Two of the
   three applications point their cover letter at an unapproved draft, and
   applying would still have been recorded as approved.
3. **Scale AI has two approved resumes** (v4 and v5), and nothing could say
   which one went out. That is the question an operator asks the day before an
   interview. `jsa prep` could not ask it either: it drilled every bullet in
   the profile, not the resume the interviewer is holding.

Nothing in the code read those pointer columns.

## Decision

**"Applied" means a human submitted it, and the tracker records exactly which
documents went out, once, at that moment.**

1. A new table, `submitted_documents`: one row per kind per application, with
   the document id, version, whether a human had approved it **at that
   moment**, a SHA-256 of the file, and the time. A trigger refuses any
   `UPDATE`. Rows can be *added* later (a missing letter), never changed.
2. **The default is the approved document, never the newest draft.** Left
   unnamed, each kind records its one human-approved document.
   - None approved: nothing is recorded for that kind, and the unrecorded
     draft is named back to the operator with the command to add it.
   - More than one approved: **refused**, listing them. A permanent record is
     not written on a guess.
   - Named explicitly (`--resume DOC`, `--cover DOC`): recorded as named,
     including an unapproved draft, **and recorded as unapproved**.
3. **Record honestly; do not refuse.** ADR 0003 decision 4 stands: the gate
   stops the agent, not the human. Sending an unapproved letter is the
   operator's right; misdescribing it is not the tracker's. The approval flag
   is per document and "all approved" is true only when every recorded
   document was.
4. **`--check`** shows what would be recorded and writes nothing, because the
   record is written once.
5. **`jsa prep` drills the resume that was sent** — its recorded `bullet_ids` —
   and says which document it used, or that it fell back to the whole profile.
6. The pointer columns stay, now documented as "latest drafted". Nothing
   outbound or historical reads them.

## A hash, and why only at submission

The SHA-256 is taken when `jsa applied` runs, so "what did I send?" has an
answer even if the file is edited or regenerated afterwards. It is not taken
at approval. Instead, a file modified after its approval is reported
(`changed_since_approval`, by mtime) as a warning. Editing your own resume
before sending it is yours to do; the record just must not imply the approval
covered the edit. All four approved files in the author's tracker were checked:
none was modified after approval.

## Rejected alternatives

- **Freeze the pointer after applying.** Keeps one column meaning two things
  depending on status, and still cannot say whether the frozen document was
  approved at the time.
- **Refuse to apply with an unapproved document.** Contradicts ADR 0003 #4 and
  makes the tracker argue with what already happened on an employer's site.
- **Default to the newest draft when nothing is approved.** That is the old
  pointer behaviour, and it is exactly how a pending letter would have been
  recorded as sent.
- **Pick the newest of two approved versions.** Right most of the time, and
  permanent when wrong.

## Consequences

- `jsa applied` on an application already applied refuses unless it names a
  kind not yet recorded. It never resets the status.
- An application applied before this change has no rows. It is not backfilled:
  nobody can say now what was sent then, and a guess would be worse than a gap.
  (In the author's tracker no application had been applied yet.)
- `documents.approved_at` exists in the schema and nothing writes it. Approval
  lives in `approvals`. Left alone here; worth removing or filling before
  publication so the schema does not suggest a fact it does not hold.
