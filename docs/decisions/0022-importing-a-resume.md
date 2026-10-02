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

## Addendum: the dashboard (plan 9), 2026-10-02

`/import` runs the same `resume_import.run()` as the command, so the two
can't disagree.

- **The token guard learned multipart.** It read the token with
  `parse_qs`, which finds nothing in a multipart body, so every upload would
  have been refused. For `multipart/form-data` it now refuses a missing or
  over-5 MB `Content-Length` with 413 before reading, then reads only the
  `csrf` part. A POST that is neither urlencoded nor multipart is refused.
- **Type by content, not by name.** The extension and the first bytes must
  agree (`SUPPORTED`); the browser's content type is ignored.
- **The resume is not kept.** It is written to a temporary directory for the
  import, and the directory goes with the request.
- **No adopt button.** Copying the draft over the live profile stays the
  user's act; the result page shows the copy command.
- Browser check (fictional resume, one model call, a copy of the tracker):
  drag-and-drop submitted the file; the model wrote an end date of
  "Present", which the year check reported as dropped. "Present" now means
  `current` and is not reported.

## Addendum: PDF resumes (plan 10), 2026-10-02

The reading step takes a PDF too (`read_pdf`); verification, identity
handling and the draft are unchanged.

- **pypdf, pinned** (6.19.0): pure Python, no system packages, so the slim
  Docker image needs nothing new. It parses an untrusted file, so it is
  pinned to a release with every published advisory fixed (checked against
  OSV on 2026-10-02), and the file is refused before parsing if it is over
  5 MB, and after opening if it is encrypted or longer than 10 pages.
- **No OCR.** Under 50 words of text over all pages means a scan: refused,
  with how to export a text PDF or use the .docx.
- **Normalizing:** NFKC (so the "fi" ligature reads as "fi"), PDF bullet
  glyphs and control characters removed, page numbers and a header or
  footer repeated on every page dropped.
- **A word broken across lines** ("coordin-" / "ated") is checked against
  the text with that line break undone, both with and without the hyphen.
  This adds no text.
- **The fixtures are built at test time** (`tests/pdf_fixtures.py`: a small
  standard-library PDF writer, and pypdf for the encrypted one), not
  committed. `tools/scan_history.py` treats any committed .pdf as a leaked
  generated document, and that rule stays strict.
- **Extraction mode**, measured: on the fixtures pypdf's plain and layout
  modes give the same lines, and neither untangles two columns. On the
  owner's own two-page PDF resume both start 16 lines with a bullet, but
  plain splits text that shares a baseline (81 lines to layout's 65).
  Imported both ways (one model call each), both kept 10 of 10 bullets with
  none dropped; layout kept 7 of 7 certifications and plain 6, one name
  broken across lines. So the reader uses layout, unless plain finds more
  bullet-led lines. (A first count said 45 to 16: it counted layout's blank
  lines as bullets, a bug fixed before this was committed.) No .docx of
  that resume exists to compare with.
- **Columns lose text and say so.** The two-column fixture keeps 1 of 3
  bullets (the other two are interleaved with the left column and dropped,
  never garbled). Above `WEAK_PDF_SHARE` = 30% of the model's bullets not
  found word for word, the report warns and suggests the .docx; the clean
  fixtures and the owner's PDF lose 0%.
