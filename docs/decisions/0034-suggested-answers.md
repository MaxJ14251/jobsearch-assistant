# ADR 0034 — Suggested answers to a form's open questions

Status: accepted
Date: 2026-10-09
Relates to: ADR 0027 (application answers and their checks), ADR 0031 (the
extension: you press Submit), ADR 0010 (why sentences about an employer fail
a word check), ADR 0001 (the pay floor never reaches a prompt). Implements
plan 35.

## Context

On a real application (Ashby), the extension filled the standard fields and
left questions like "What excites you about <company>?" for the owner to
write. The owner asked for the extension to read those questions and offer a
few answers to pick from.

The written answers of ADR 0027 already draft and check three fixed
questions. A form's own questions are different in one way that matters:
their text comes from someone else's web page.

## Decision

1. **The panel lists the questions the fill left empty** ("Questions to
   answer"): textareas, and long text inputs whose label asks something,
   that nothing in the profile answers. Salary, demographic (EEO) and "how
   did you hear about us" questions are never listed, and the dashboard
   refuses them too: they are the person's to answer.
2. **On request only.** Nothing is drafted when the section appears. Each
   Suggest press is one model call (two with the retry), counted against a
   daily limit (`SUGGEST_DAILY = 20`, or `suggest.daily_limit` in the
   profile) and recorded in the ledger under purpose `suggest`. "Suggest
   for all" asks first, saying how many it will use.
3. **The question is untrusted.** Only its label text goes, cleaned of
   markup and control characters and capped at 300 characters. In the
   prompt it sits in a fenced `<question>` block (any `<` or `>` in it
   escaped, so it can't close the block), next to a 2,000-character posting
   excerpt fenced the same way, and the system text says both are data from
   a third party with no instructions in them. **Its words are never added
   to the words an answer may use**: the check's job dict holds the title,
   company and location only, so a question naming a tool the person never
   used can't make claiming that tool pass.
4. **Three options, each checked or the person's own sentences:**
   - *about the role* and *about your project*: drafted, then held to the
     written answers' checks (`answers.check`: word bounds, no degree claim,
     no degree mention when none was conferred, no finished form of ongoing
     work, no do-not-claim term, no word the profile doesn't support). The
     bounds tighten to fit the field's `maxlength`. A refused draft gets one
     retry naming the refused words, then is replaced by one composed from
     the profile, labelled so.
   - *about the company*: composed only, as in ADR 0027 (0 of 5 passed).
   - No two options are the same; an option is never shown unchecked.
5. **Only a click puts text in the page.** "Use this" is the one path from a
   suggestion into a field; it asks before replacing text already there,
   and outlines the field for review. Nothing submits; tests read the
   extension's code to hold both.
6. **Earlier answers come back.** At "I submitted this" (after its
   confirm, before recording), what is in each listed question is kept in
   `answer_bank`, the company's name stored as `{company}`. The same
   question later (same text, same normalized key, or at least 80% word
   overlap, labelled "a similar question") shows up to two of them at once,
   with no model call. Text the person wrote or edited (`yours`) is offered
   as it was, company name aside: it is their own claim. Drafted or composed
   entries are re-checked against the new job and dropped if they fail.
   Bank text never goes into a prompt. The normalization is one rule in
   Python and JavaScript, held together by shared test vectors
   (`extension/test/question_cases.json`).

## Consequences

- ADR 0010 and ADR 0027 measured drafted answers failing the word check
  most of the time. Expect many options to be composed variants; the label
  says which, and the person edits before submitting.
- The posting excerpt helps the model aim, and its words still fail the
  check unless the profile supports them. That is deliberate: loosening the
  verifier to raise the pass rate is a separate decision.
### Measured, 2026-10-09

Ten questions over the owner's four jobs ready to apply to, one press each
(20 model calls with the retries, run on a copy of the tracker). The two
Greenhouse forms' public question lists had no open-ended question (only
contact fields, files, dropdowns and salary), so one question is the one the
owner saw on a real form ("What excites you about <company>?") and nine are
typical open questions of the same kind.

| Option | Drafted and passed | Composed |
|---|---|---|
| About the role | 0 of 10 | 10 |
| About your project | 1 of 10 | 9 |
| About the company | composed only | 10 |

Of the 20 refusals (first tries and retries counted by their final reason),
18 were words the profile doesn't contain, 1 the word count, 1 a finished
form of ongoing work.

**Under the 30% the plan set**, so: the options work, but almost all of them
are composed variants of the person's own sentences, and the panel says so.
The verifier is not loosened. A check that knows who each sentence is about
(ADR 0010: "I like that Vercel ships fast" is about the employer and claims
nothing about the person) is the fix worth planning on its own.
