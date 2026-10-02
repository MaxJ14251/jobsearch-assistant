# ADR 0022 — Importing a resume

Status: accepted
Date: 2026-10-02
Relates to: ADR 0005 (the profile is the operator's description of their own
work), ADR 0016 (what a model reads). Implements plan 8 of the planning
session.

## Context

Every job is scored against `profile/master_profile.yaml`, and every document
is built from it. A newcomer writes that YAML by hand from the example, and
the README told them to budget most of their first hour for it. With the repo
public, that is the first wall a new user hits. Most people already have the
same material in a resume.

The owner chose, on 2026-10-02, **a command first**: `jsa import-resume
<file.docx>`. A dashboard drop zone and PDF input are later plans.

## Decision

`jsa import-resume` reads a .docx resume and writes a **draft** profile,
`profile/master_profile.draft.yaml`, then prints what `jsa doctor` would
still block on and the top matches the draft would give.

1. **Copy, never reword.** One model call splits the resume into sections.
   `resume_import.verify()` then keeps only what appears **word for word** in
   the resume (case, whitespace, bullet glyphs and smart quotes folded):
   every bullet, employer, job title, school, field, project, certification
   and skill. Dates must contain only years the resume contains. Anything
   else is dropped and listed with its reason, never softened. This is
   stricter than `tailor.verify_draft`, which allows shortening: an import
   has nothing to shorten.
2. **Suggestions are marked as suggestions.** Tags, families and target
   titles are the model's proposals, not claims; each carries
   `# suggested: check` in the draft.
3. **Identity is split off locally.** Name, email, phone, street, ZIP, links
   and the home "City, ST" are found with local patterns and replaced with
   `[contact]` before the prompt is built; `tailor.scrub_prompt` then checks
   the prompt against them and refuses before any network call. A street,
   ZIP or city counts as identity only in the contact block (above the first
   section heading), since every job on a resume has a location too. A field
   the patterns can't find is left empty with a TODO; nothing is guessed.
4. **The degree is never guessed.** `credential` is always empty with a TODO,
   whatever the model returns; the resume's own education line is shown
   beside it as a comment. `doctor.check_credentials` already blocks an
   empty credential.
5. **The live profile is never written.** The command refuses an `--out`
   that names `master_profile.yaml`, and refuses to replace an existing draft
   without `--force`. Adopting the draft is a copy the user makes. The draft
   and anything dropped into `profile/` as .docx or .pdf are gitignored, and
   the secret scanner skips the draft as it skips the live profile.
6. **Decisions stay with the user.** Work authorization, the pay floor,
   sponsorship, relocation, summaries and `aspirational_do_not_claim` are
   left as TODO. The draft suggests "Remote (US)" and the resume's home city
   as locations, so the preview has something to score.
7. **The preview is read-only and partial.** It scores the tracker's open
   jobs in memory against the draft and writes nothing. Those jobs were found
   with the *current* profile (discovery keeps only what scored above its
   minimum), so the preview says so and points to `jsa discover` after
   adopting.
8. **.docx only.** python-docx is already a dependency; the reader takes body
   paragraphs, tables (resumes often use a two-column table) and page
   headers and footers (where contact details often sit). A PDF is refused
   with how to save one as .docx.

## Consequences

- A newcomer gets a draft with their real bullets in a few seconds, and a
  list of what is left to decide, instead of a blank template.
- A bullet the model rewords is lost, not corrected; the list of dropped
  items tells the user what to copy across by hand.
- The resume's text, minus contact details, goes to the configured model API
  (SECURITY.md).

## Measured

2026-10-02, the owner's resume as downloaded from the dashboard (a
tailored resume this tool rendered, so an easier case than a hand-made one).
Counts only.

- First run: 1 job, 1 of 2 projects, 2 of 5 bullets kept; 1 item reported
  dropped. Two bugs, both fixed and covered by tests:
  - a project heading "Name - <repo link>" came back as "Name -
    [contact]" (the link had been removed as contact detail) and failed the
    check, which also took its 3 bullets;
  - those 3 bullets were dropped **silently**: an entry that failed took its
    bullets with it unreported. Now each is listed, "dropped with its
    project".
- A third bug from the same run: five digits inside the GitHub handle were
  read as a ZIP code (the resume prints none). The ZIP pattern no longer
  matches inside a word or link.
- After the fixes: 1 job, 2 projects, 5 of 5 bullets, 2 certifications,
  1 school, 13 skills; 0 dropped. Name, email, phone and both links match the
  live profile. 4 of 5 bullets are identical to a profile bullet; the fifth
  is the tailored resume's shortened version, copied as the resume has it.
  One project name carries extra words from the resume's heading line;
  the user's review is where that gets trimmed. The credential was left
  empty, and doctor listed 4 blocking items (work authorization, pay floor,
  no summary, the credential).
