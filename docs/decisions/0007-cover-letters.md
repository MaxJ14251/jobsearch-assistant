# ADR 0007 — Cover letters

Status: accepted
Date: 2026-09-18
Extends: ADR 0005 (the added-words guard) to prose that has no source bullet.
Decided by: /software-design-council, chaired during the "cover letters have
never been generated" goal.

## Context

`jsa tailor <id> --kind cover-letter` had existed for weeks and had never been
run. The tracker held 16 documents, all resumes.

`cover_body` built a letter by concatenating the verified summary and the
first three verified bullets, with no second model call. The reasoning was
sound on its face: every sentence had passed `verify_draft`, and fresh prose
would have had nothing checking it.

**Then it was read.** Two letters were generated for real postings (Replit,
Premium Support Engineer; Rocket Lab, Software Engineer II – Robotics), 137
and 148 words:

- no greeting, no closing;
- no sentence saying what was being applied for;
- the company named only in the "Re:" line;
- resume bullets as paragraphs, in selection order, so the support letter
  opened with an AI project.

Every sentence was true. The artefact was not a letter, and no employer would
read it as one.

## Decisions

### 1. A second model call writes the prose

A letter needs sentences that do not exist in any profile — "I am applying
for…", "I would welcome the chance to talk". Extraction cannot invent them,
which is exactly why the composed version was not a letter.

Rejected: dropping cover letters entirely (employers ask for them); a fixed
template with generated slots (the same checking problem, plus more
machinery).

### 2. The whole letter is held to an allowed vocabulary

Not sentence-by-sentence against a source bullet — prose has no single source.
Every meaningful word in the letter must stem-match one of:

- the candidate's own profile (`_whole_profile_stems`, the test the resume
  summary already gets);
- a fixed list of connective and ordinary English words in `jsa/letter.py`;
- the role title, company and location — which are database columns, not
  model output.

**The rule for that list: no entry may name a skill, a tool, an outcome or a
quality.** "communication", "leadership" and every technology stay out, and
that is deliberate: those are claims, and claims come from the profile.

### 3. The posting's words are NOT allowed

Tempting, because the posting sits in the tracker. Refused on evidence: ADR
0005 records that telling the model to "borrow the posting's own vocabulary"
is what produced "strict turnaround requirements", "SLA adherence" and
"motion-control software" on real resumes.

This also answers what a letter may say about the employer. It may name the
company and the role. It may not praise them, because "industry-leading" is
not in the profile either. One mechanism, both problems.

### 4. Three checks the vocabulary rule cannot see

- **The degree.** No letter may contain degree words at all (degree,
  bachelor's, B.S., graduated, diploma…). The profile records coursework
  without a conferred degree; there is no honest sentence in this class.
- **Unfinished work.** For every project not marked complete, the finished
  forms of that project's own verbs are banned: "Building" may not become
  "Built", "Goal: reduce" may not become "Reduced". A word the profile itself
  uses is exempt — the profile says "Architected closed-caption generation",
  so a letter may too.
- **Banned terms**, from `aspirational_do_not_claim`, over the whole letter.

Plus structure: a greeting, a closing, the role title, the company, and
120–350 words.

### 5. A failing letter falls back; it is never faked and never absent

Two attempts. The second names what failed and lists the words to avoid,
rather than re-rolling blind. If both fail, the letter is composed from
verified sentences with proper scaffolding — greeting, a line stating the
role, the summary, the strongest bullets, a closing.

`documents.note` records that this happened and why. `jsa review <id>` and the
dashboard both print it. **A template must never read as drafted prose.**

## Measurements

| Stage | Result |
|---|---|
| Original composition (docs 17, 18) | Unsendable: bullets in a row, no greeting, no closing |
| First strict rule (docs 19, 20) | Both letters REFUSED, both fell back |
| Words that refused them | "every", "field", "gave", "spent", "translates", "needs", "orchestrate", "writing" |
| After the ordinary-English list and the naming retry (docs 21, 24) | Both passed, 176 and 182 words |

The strict rule was rejecting ordinary English, not claims. The words above
say nothing about anyone. Widening the list to common words — while keeping
every skill, tool and quality out of it — is what made an honest letter
possible. "communication" is still refused, and that is the line: it is a
claimed quality, and the profile decides those.

The retry matters as much as the list: one honest word out of place should
cost a rewrite, not the letter.

## Consequences

- A cover letter now costs two model calls: one for the resume draft it is
  built from, one for the prose.
- Letters are held to a stricter standard than resumes in one respect —
  resumes may revert a bullet, letters have no per-sentence source to revert
  to, so the whole letter falls back.
- The check will sometimes refuse an honest sentence. The fallback bounds the
  cost; the operator can widen their own profile's wording if a word they
  want keeps being refused.
- `documents.note` is new. It is for how a document was produced, when that
  is not obvious from its other columns.
- The word lists are a judgment call, and the first two real letters are the
  only evidence behind their current contents.
