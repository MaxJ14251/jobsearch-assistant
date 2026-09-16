# ADR 0005 — Role kind and tag vocabulary

Status: accepted
Date: 2026-09-16
Decided by: /software-design-council, during the "close the gap between your
tags and how postings talk" goal. Amends the selection behaviour described in
the tailoring goals; does not change ADR 0001-0004.

## Context

Tailored resumes for support roles selected the wrong bullets. Job 421, Replit
"Premium Support Engineer", produced a resume of AI project work with no
customer-facing material, although the posting says "customer" nine times and
"support" ten.

The goal framed this as a tag-vocabulary problem. Measurement showed that was
the smaller half.

### Evidence

- 47 distinct bullet tags, 8 of them dead -- matching 0 of the 514 postings in
  the tracker: `b2c`, `captions`, `cloud-native`, `customer-facing`,
  `ffmpeg-adjacent`, `gemini`, `media-processing`, `solution-selling`.
- On job 421, `b_vid_goal` (ai_engineering) scored 4.85 from tags **plus 3.0
  from the family bonus** = 7.85. `b_riv_sell` (sales) scored 1.69 with no
  bonus. Even had `customer-facing` matched perfectly, it would reach roughly
  3.7 and still lose.
- The family bonus came from `track`, which is binary. A Premium Support
  Engineer is stored `track='engineering'` and so inherited the AI-engineering
  preference -- the wrong preference for a support role.

So: dead tags cost a couple of points; the binary track cost the match.

## Candidates, measured on three real jobs

| Candidate | 421 Replit support | 260 Rocket Lab robotics | 389 Scale AI |
|---|---|---|---|
| Baseline | all AI | all AI | all AI |
| Split every hyphenated tag | all AI | `inst_install` returns | AI |
| Family from the posting's own tag mass | 1 sales bullet | **all fire-alarm + sales** | AI |
| No family bonus | 1 sales | **all fire-alarm + sales** | AI |
| **Role kind from the title** | **sales + field work** | **all AI, unchanged** | **AI, unchanged** |

The two posting-driven candidates failed badly on 260. A robotics posting is
full of "systems", "hardware", "troubleshooting" and "ownership", which fire on
the fire-alarm and sales tags, so those families won the mass. That is exactly
the false-positive failure tag rarity weighting was added to prevent, and
deriving preference from tag mass reintroduced it in amplified form. The family
bonus was what had been protecting 260.

## Decisions

### 1. Family preference comes from the role kind, and the role kind comes from the title

Titles are the least noisy text in a posting: short, specific, and not
marketing prose. Three kinds:

| Kind | Families preferred, with bonus |
|---|---|
| engineering | ai_engineering 3.0, data_ml 3.0 |
| support | sales 3.0, technical_field 3.0, ai_engineering 1.5, data_ml 1.5 |
| sales | sales 3.0, technical_field 3.0 |

A sales track, or a sales title, is `sales`. A support title is `support`.
Everything else is `engineering`.

Support keeps AI work at half weight rather than none: a support role at an AI
company genuinely values it, and the resume should show both.

**The support pattern requires customer context.** A first draft matched any
title containing "support", and 5 of its 29 matches were hardware roles --
"Ground Support Equipment", "Life Support Systems", "Fleet Support",
"Tooling Design and Support". A match the operator would dispute is a bug.
"Support" now counts only as `technical|premium|customer|product|it support`
or `support engineer|specialist|analyst`, alongside customer success,
solutions engineer/architect, forward deployed, implementation and onboarding.
That catches 24 titles in the tracker, none of them hardware. The rejected
titles are pinned in a test.

**`jsa tailor` prints the role kind it used**, so a misclassification is
visible on every run rather than discovered later in the document.

### 2. Dead tags fall back to their components, narrowly

A tag is matched as a phrase first. Only when that phrase matches **zero**
postings, and the tag is hyphenated, does it fall back to its component words
-- filler words dropped (facing, adjacent, native, based, driven, focused,
oriented, processing, selling), each at 0.6x its own measured rarity weight.

Splitting every tag was tried and rejected: it let `b_inst_install` back into
the robotics resume.

Measured effect: 8 dead tags become 4. `customer-facing` now fires on
"customer" (227 postings), `cloud-native` on "cloud" (107), `media-processing`
on "media" (9), `solution-selling` on "solution" (288). The last is common
enough that its weight barely registers; accepted.

Still dead, and only the operator can fix them: `b2c`, `captions`,
`ffmpeg-adjacent`, `gemini`.

### 3. Nothing is persisted, so nothing can drift

The fallback is computed at selection time from the profile and the tracker.
There is no synonym map on disk. ADR 0003 decision 2 exists because a cache
"kept in sync by the app layer" is how two copies of a fact diverge; the
simplest way to honour it is to have no cache.

On a fresh clone with no postings, `tag_weights` returns nothing, no tag is
known to be dead, and no fallback happens. The safe default is no
reinterpretation.

### 4. Dead tags are reported, never rewritten

`jsa tags` lists every tag with how many postings it matches, marks the dead
ones, and shows which component words would revive them. It does not edit
`master_profile.yaml`. The profile is the operator's description of their own
work, and the README calls it the contract.

### 5. Safe by construction

Matching only decides which of the operator's own bullets are chosen and in
what order. No mapping, role kind or fallback ever produces text. The text is
always the profile bullet or a rewrite that passed `verify_draft`, and
`aspirational_do_not_claim` still governs every output. A test asserts that
selection returns only bullets present in the profile.

### 6. The fabrication guard now checks what a rewrite ADDS

Found while reading the first resume this change produced. It led correctly
with sales experience -- and its bullets said things the profile does not:
"clear, confident communication during high-priority support scenarios",
"hands-on troubleshooting experience with integrated hardware and software
systems", and, on an in-development project, "Reduced a multi-hour workflow"
where the profile says "Goal: reduce".

The cause was in the guard, not the model. `source_overlap` measured
**recall** -- how much of the source survived. It never looked at what was
appended. A rewrite that kept a whole bullet and bolted on a new clause scored
*higher*, not lower.

Replayed over every document generated before the fix:

| Documents | Added share | What was added |
|---|---|---|
| 1-3 | 0.00-0.12 | tense changes only |
| 4-6 | 0.20-0.55 | invented claims, cross-project facts, invented summaries |

**This is a correction to earlier work.** Documents 4 and 5 were read and
judged to "read well"; they read well because they were embellished. They are
still pending approval and should be rejected.

The new check stems each word and asks whether it is supported by the source
bullet, that bullet's own tags (the operator's words about it), or a sibling
bullet of the same experience or project entry (true facts about the same
work). Above `INVENTION_CEILING = 0.15` of unsupported words, the bullet
reverts to the profile text. With siblings and stems allowed, legitimate
re-presentation measured at most 0.10 and every fabrication at least 0.20.

The summary is held to the same test against the whole profile, and reverts to
the chosen summary variant if it fails.

### 7. Ongoing work may not be rewritten as finished

Stemming cannot see this -- "reduce" and "reduced" share a stem. So it is its
own rule: when the source leads with "Goal:" or a progressive verb
("Building", "Integrating"), a rewrite leading with a past-tense verb reverts.
Replay showed it was older than the prompt change: document 3 had already
turned "Building" into "Built" and "Integrating" into "Integrated" on projects
marked in development.

### 8. The prompt's job is to make an honest rewrite the easy path

Three settings, each measured on live output:

| Prompt | Result |
|---|---|
| three prohibitions, one instruction | 16 of 18 bullets byte-identical |
| "do not return a bullet unchanged", "borrow the posting's vocabulary" | rewrites, mostly fabricated |
| "if it already fits, return it" | 13 of 13 byte-identical |
| **work only with the bullet's own words: reorder and cut, never add** | **11 of 13 identical, 2 honest cuts, 0 words added** |

The last is kept. The two edits it made were sensible for a support role --
trimming "that supported a move into sales" and "close deals".

**Plainly: with this model tier, tailoring is mostly selection.** Bullet text
changes little. That is the honest result. The earlier appearance of heavy
rewriting was the appearance of fabrication.

Order of checks in `verify_draft`: structural refusals first (unknown id,
near-zero overlap), then banned terms against the text **as generated**, then
per-bullet reverts. Checking banned terms after reverting would let an attempt
to claim a forbidden term pass quietly.

## Consequences

- Support roles now select customer-facing and field experience.
- No rewrite can add an unsupported claim or turn ongoing work into a
  finished result; each revert is printed with its reason.
- Engineering roles are unchanged, including the robotics case that motivated
  rarity weighting.
- No schema change, no profile change. Every part of this is cheap to reverse.
- The title patterns are English and US-centric (consistent with ADR 0002) and
  will miss some support roles. The printed role kind is how that surfaces.
