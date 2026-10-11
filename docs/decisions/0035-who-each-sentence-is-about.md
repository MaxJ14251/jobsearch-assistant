# ADR 0035 — A check that knows who each sentence is about

Status: accepted; measured, switched off
Date: 2026-10-10
Relates to: ADR 0007 (the letter's word check; decision 3, posting words),
ADR 0010 (why sentences about an employer fail), ADR 0027 (written answers),
ADR 0034 (suggested answers). Implements plan 37.

## Context

Drafted text almost never passes the word check: ADR 0010 measured 1 of 9
outreach drafts, ADR 0027 0 of 5 for both why-this-role and why-this-company,
and ADR 0034 1 of 20 suggested answers, 18 of the 20 refusals being "words
the profile doesn't contain". The check treats every word as a claim about
the person, so "Vercel ships developer tools" fails on words that describe
Vercel.

## Decision

1. **Split, then sort** (`jsa/subject.py`, no model). A draft is split into
   sentences (on `. ! ? ;` and line breaks; commas, dashes and colons never
   split). Each sentence is:
   - **employer**: it STARTS with the employer's full name (possessive
     allowed), "the/this role/position/team/company/job", "your" or "you",
     or "its/their/they/it" right after an employer sentence; and it has no
     first-person word (I, my, me, we, our, us…), no word standing in for
     the person (candidate, applicant, someone, whoever, applying, hire, "a
     person", "the right person"…), none of the person's own names, and no
     self-referring opener ("Having…", "As a…", "After years…").
   - **strict**: everything else — about the person, mixed, or unclear.
     Every doubt goes here; a wrong split only makes fragments with no
     employer subject, which are strict.
2. **The attitude frame** (`ATTITUDE_FRAME`, measured both ways). "I'm
   drawn to / I admire / I like / I appreciate / I'm excited by / I'm
   interested in / What draws me to / What excites me about" + an employer
   subject is an attitude, not a claim of skill, and the rest is checked as
   an employer sentence, only if that rest has no further first person or
   stand-in, no comma and none of: and, because, since, which, who, where,
   while, as, having, after, so, when, "that I".
3. **What each is held to** (`letter.checked_sentences`):
   - **strict**: exactly today's rule — the profile's words, connectives,
     identity, and the job's title, company and location. Nothing changes.
   - **employer**: those words **or the posting text the drafter was
     given** (the Suggest prompt's 2,000-character excerpt; the written
     answers' visible posting). Never a form's question, never a banked
     answer. No negation (not, no, never, -n't, without, lacks…), and every
     number must appear verbatim in that posting text.
   - **every sentence**, whoever it is about: no degree word (the degree was
     not conferred), no do-not-claim term, no finished form of ongoing work
     (all checked over the whole text, as before).
   - **the draft**: at most `MAX_EMPLOYER_SHARE = 0.6` of its sentences
     about the employer, and at least one not: an answer must say why the
     person fits.
   Refusals name the sentence and its kind ("employer sentence 2: … not in
   your profile or the posting"), and the retry asks the model to avoid
   exactly the refused words.
4. **Where, and behind a switch.** `answers.check(…, posting_text=…)` uses
   it; the written answers and Suggest pass their posting text through
   `answers.subject_aware`, which returns None, the old whole-text check,
   while `answers.SUBJECT_AWARE` is False. No prompt changes. The cover
   letter and outreach do not use it (see Consequences).
5. **The switch turns on only after a measurement** (`tools/
   measure_verifier.py`, paid, run by hand with the owner's OK): the same
   ten questions as ADR 0034, each drafted once (role, project and company)
   with one retry, every draft checked the old way and the new way, frame on
   and off. The owner reads every draft that passes only under the new check.
   On only if **no** draft says something about them that isn't in their
   profile, and the role option passes at least 5 of 10 (against 0 of 10).
   The company angle is drafted (not composed-only) only at 7 of 10 with no
   leak. A leak becomes a red-team case, the sorter is fixed, and the saved
   drafts are re-checked with no new calls (`--recheck`).

## Tests

- `tests/test_subject.py`: the sorting rules (employer, strict, the frame
  and what breaks it), and every refusal the suites already held — the
  letter's industry-leading and SLA cases, the answers' synergy, Google,
  degree and finished-work cases, outreach's "I handle customer tickets",
  Suggest's Kubernetes claim — run through the new path with the posting
  carrying the very words: each still refused.
- `tests/evals/verifier/answers.yaml` (in `tools/eval_report.py` too): 4
  passes and 16 red-team refusals, each with its reason, on the fictional
  eval profile.
- `tests/test_suggest.py`: off by default changes nothing; on, an employer
  sentence in the posting's words passes and "I built" + the same words is
  refused.

## Consequences

- ADR 0007 decision 3 holds for every sentence about the person; posting
  words are allowed only in sentences about the employer, and so far only
  in written answers and suggestions, and only once switched on.
- **Measured 2026-10-10, and left off** (below).
- Cover letters would need this ADR's switch, a dated amendment to ADR 0007,
  and the posting passed to the dashboard's letter re-check, which has none
  today. Outreach would need the same, and is tightened first (plan 38).

## Measured, 2026-10-10

The ten questions of ADR 0034's run, over the owner's four ready jobs: one
draft per question with all three angles, one retry for each angle the new
check refused (`tools/measure_verifier.py`; 20 model calls; drafts kept
locally, never committed).

| Angle | Old check | New check | New, frame off | New, after one retry |
|---|---|---|---|---|
| About the role | 0 of 10 | 0 of 10 | 0 of 10 | 2 of 10 |
| About your project | 0 of 10 | 0 of 10 | 0 of 10 | 2 of 10 |
| About the company | 0 of 10 | 0 of 10 | 0 of 10 | 0 of 10 |

No draft passed only under the new check, so there was nothing for the
owner to read and no leak. The retry's passes pass the old check too.

**Why the new check didn't help:** of the 143 sentences in the 30 first
drafts, 110 had a first-person word and 31 no clear subject; only 2 were
about the employer. The model writes "My work on X mirrors <company>'s…",
mixing the person and the employer in one sentence, and such a sentence is
checked strictly, as it must be. The refused words were the person's
("reliable", "verification", "critical", "mirrors"…), not the employer's.

**Decision:** under the plan's bar (role at least 5 of 10), so
`SUBJECT_AWARE` stays False and the company angle stays composed-only. The
sorter is right; the drafts don't separate who each sentence is about. The
next step, if wanted, is a prompt that asks for sentences about the
employer to start with its name and stay apart from sentences about the
person: a prompt change, so its own plan, measured on the same questions
(the saved drafts can be re-checked with `--recheck`).
