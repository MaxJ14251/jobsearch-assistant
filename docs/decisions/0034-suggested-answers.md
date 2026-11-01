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

## The decision this left open, 2026-10-10 (plan 37)

"Loosening the verifier … is a separate decision": ADR 0035 is that
decision, for sentences about the employer only, behind a switch that is off
until measured on these same ten questions.

## The split prompts, 2026-10-11 (plan 39)

Suggestions now draft about the role 10 of 10, about your project 9 of 10
and about the company 7 of 10 after one retry (ADR 0035), against 1 of 20
before. The company option is drafted too. The panel says: "Most are drafted
and checked against your profile and the posting (26 of 30 when measured);
the rest are made from your own sentences and labelled." The wording comes
from the dashboard (`suggest.panel_note`), so it follows the switch.

## Options have to answer the question, 2026-10-31

The owner reported that options didn't truly answer "Why do you want to work
at <company>?" or "Walk us through your proudest achievement". Across the 14
real presses on record, 25 of 42 options were drafted and 17 composed, against
the 26 of 30 measured above; on the "why" press, all three were composed. Two
separate causes:

- **Composed options never read the question.** A stock lead, one bullet and
  the summary, whatever was asked, offered as if it were an answer.
- **Drafts weren't asked to answer.** The prompt described each option by
  topic, so passing drafts listed the person's work and recited the posting.
  10 of the 30 drafts saved on 2026-10-11 open with a sentence about the
  employer. The check can't see this: it tests that claims are supported, not
  that they respond.

Decided, with the check itself unchanged, including its word lists and bounds:

- The split prompt says each answer responds to what is asked, in its first
  sentence, about the candidate. "I want to work at <company> because…" already
  passes the check. "Proudest" and "excited" don't, so the prompt says plain
  words.
- A draft refused only for words in some sentences loses those sentences, if
  its first sentence stands and is about the person and what's left passes the
  whole check. Any other refusal (a degree, a do-not-claim term) drops the draft.
- Up to three drafted answers are shown, never composed filler beside them. If
  none passes, one block of the person's own sentences is shown, with no lead,
  marked "Not an answer to the question". The panel note no longer quotes 26 of
  30, which real use didn't bear out.

With the switch off, all of this is as it was. Replaying sentence removal on
the 30 saved drafts changes little: 4 were refused, and the only one it could
save opens about the employer, so it stays out. Those drafts came from the old
prompt. **Whether the new prompt gets answers that respond has not been
measured**: that takes paid model calls.

## Options have to be about this listing, 2026-10-31

The owner reported that answers read as rewordings of one answer. Across 21 real
presses on 8 jobs, 60% of sentence uses sat in a repeated sentence. Causes: the
first 2,000 characters of a posting (18-50% of it) open with the company's blurb,
the question chose nothing, every option drew on the same bullets, 4 of 13 question
types asked for facts nothing can draft, and a profile holds no reason for wanting
a particular job.

Decided, with the check itself unchanged (`jsa/listing.py`):

- The question is sorted first, with no model call. Education gets the profile's
  exact line; start dates, deadlines and "where did you hear" are the person's.
- The excerpt is chosen by section: the role and what it asks for, the blurb in
  full only when the question is about the employer. Pay, perks and legal text
  never go. The check reads the same excerpt.
- Each option's notes come from a different project or job, by what the job's
  resume uses and what the question names. Matching posting lines to bullets by
  shared words was tried first and rejected before any model call: it paired a
  Node.js line with an unrelated sales bullet over "security" and "solutions".
  The model picks the duty, since that takes judgement.
- An option that repeats another or opens by describing the employer is refused.
  These only tighten.
- An optional line typed by the person may be used in that press as their own
  claim, after the degree and do-not-claim checks. It is not stored.

### Measured, 2026-10-31

13 presses on 8 jobs (25 paid calls), on the first version of this prompt, with
three options for a "why" question:

| | Before | First version |
|---|---|---|
| Sentence uses in a repeated sentence | 60% | 32% |
| Options in one press opening alike | seen | 0 |
| Options drafted, not composed | 42 of 55 | 25 of 25 |
| Answers to different jobs at least 60% alike | | 8 of 270 pairs |

The author's reading, which the numbers don't show: **this did not yet reach the
goal.** Many answers put "because" in front of a copied bullet and followed it with
an unrelated line of the posting; 14 of 25 lost a sentence to the check, 9 of those
for a verb form alone ("takes" where the posting says "Take"); one named a
requirement the person doesn't meet; several third options came from unrelated work.
No claim about the person went beyond the profile.

So the prompt then asked for a duty from the posting, no whole notes, and no duty
the notes don't meet; a question got two options; and a question naming something
some work touches uses only that work.

### Measured again, 2026-10-31

8 presses on 7 jobs (13 paid calls), two with a placeholder for the person's line;
then one more press (2 calls) after the duty's verbs were put in the -ing form.

| | First version | Second version |
|---|---|---|
| Sentence uses in a repeated sentence | 32% | 32% |
| Options that lost a sentence to the check | 14 of 25 | 2 of 14 |
| Tied to this listing | 22 of 25 | 14 of 14 |
| Answers to different jobs at least 60% alike | 8 of 270 | 1 of 83 |

The person's line went into the answers as asked, and answers built on it read most
like answers. The author's reading: **still short of the goal.** The new prompt made
its own template, a copied note, "This role involves", another copied note, in all
14 answers, with nothing saying how the work and the duty connect. Most personal
sentences are still close to a bullet. One press turned "Designing" into "I designed
and am iterating": the unfinished-work list leaves out a verb another project uses
in its finished form, so the check can't see it. That gap is open.
