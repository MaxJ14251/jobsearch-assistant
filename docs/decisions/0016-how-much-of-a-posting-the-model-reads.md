# ADR 0016 — How much of a posting the model reads

Status: accepted
Date: 2026-09-27
Relates to: ADR 0001 (work authorisation), ADR 0005 (role kind and tags),
ADR 0007 (cover letters).

## Context

Four modules sliced the job description themselves, at three lengths nobody
had ever reconciled: `tailor` and `prep` at 4,000 characters, `enrich` at
6,000, `outreach` at 2,000. A resume was therefore tailored from less of the
posting than the extraction pass had already read, and nothing about the
draft said so.

**Measured 2026-09-27 over 1,266 postings** in the author's tracker. Median
length 6,092, mean 6,187, longest 23,227. 92% are longer than 4,000; 52% are
longer than 6,000. Where the parts that decide a draft sit, as a share of the
postings that contain them at all:

| what the model is shown | 4,000 | 6,000 | 12,000 |
|---|---|---|---|
| a requirements heading | 90% | 98% | 100% |
| a degree line | 79% | 98% | 100% |
| a years-of-experience line | 93% | 100% | 100% |
| a clearance line | 84% | 96% | 100% |
| a work-authorisation line | 28% | 84% | 100% |

One posting in five that states a degree requirement stated it where the
tailoring model could not see it. Four in five stated work authorisation
there, which is the thing ADR 0001 exists to get right.

## Decision

**One function decides what every model call reads: `jsa.posting.visible()`.
The budget is 12,000 characters, and the window is a plain prefix.**

### Why 12,000

Raising the cap costs tokens only on the postings that are long, and most are
not:

| cap | still cut | mean window |
|---|---|---|
| 4,000 | 92.0% | 3,860 chars (~965 tokens) |
| 8,000 | 14.7% | 6,018 |
| 10,000 | 2.2% | 6,154 |
| **12,000** | **0.3%** | **6,174 (~1,543 tokens)** |
| 24,000 | 0.0% | 6,187 |

The curve is flat past 10,000: going from 12,000 to no limit at all buys four
postings and thirteen characters of average prompt. 12,000 leaves 99.7% of
postings complete for about 580 extra tokens per call. Verified end to end
against the live models: an 11,875-character posting drafted in 19.7 s on
`nemotron-3-ultra-550b-a55b`, six bullets, no fabrication refusal.

### Why a prefix, and nothing cleverer

Two alternatives were measured and rejected:

- **Strip boilerplate to make room.** Only 6% of the first 4,000 characters
  is EEO text, benefits and "about us" — that material lives at the *end* of
  a posting, which is the part already being cut. Stripping it raised the
  work-authorisation figure from 28% to 41% and everything else by a point or
  two, in exchange for a rule that can silently delete a requirement worded
  like a benefit ("we offer mentorship to engineers with 5+ years").
- **A head plus a tail**, to catch the work-authorisation line at the bottom.
  Measured *worse* than simply reading more: 3,000 + 1,500 sees 63% of degree
  lines against the old 79%, because it cuts the middle, and the middle is
  where requirements live.

So whatever reaches a model is a contiguous slice from the start: nothing
reordered, nothing rewritten, no line lifted out of the middle. A model that
sees a sentence the posting does not contain is the fabrication problem in a
different coat. `visible()` stops at a blank line when one falls in the last
tenth of the budget, so the window ends between sections rather than
mid-sentence — a tidy boundary is worth a few hundred characters, not a
requirement.

Outreach keeps a smaller budget, 2,000, because it is answering "why this
role" rather than writing against requirements — but it asks the same
function for it.

### What the operator is told

`jsa tailor` and `jsa prep` already announced truncation. They now say what
was left out and quote the words it resumes at, so the boundary can be found
in the posting instead of counted to:

> note: posting is 15,200 chars; the model read the first 11,948 and not the
> last 3,252, which begin "Benefits and perks We offer comprehensive..."

Since this change that fires on 0.3% of postings rather than 92% of them,
which is what makes it worth reading.

## What this does not fix, measured

Honesty about the size of the win, because the headline numbers above
overstate it:

- **Bullet selection never had this problem.** `select_bullets`,
  `pick_summary` and `keyword_gap` all read the *whole* description and always
  did. Only the LLM prompt was truncated, so what the cap changed is the
  wording the model produces and the facts it extracts, not which of the
  operator's bullets get picked. Drafting one long posting at 4,000 and at
  12,000 selected the identical six bullets and reported identical gaps.
- **Extraction was already mostly fine at 6,000.** Only 43 postings state
  their requirements *only* after character 4,000, and enrich's own budget
  was 6,000, where 98% of degree lines and 96% of clearance lines were
  already visible. An A/B extraction over those 43 was still running when
  this was written; whatever it shows, the population it can affect is 3% of
  the tracker.
- **Four postings in the live tracker hold facts that contradict their own
  text** (jobs 288 and 514 record no degree requirement while the posting
  states one). Re-extracting them at every budget from 4,000 to 12,000
  returns `degree_required=True` regardless, so **truncation is not what
  produced those rows** — an older model or prompt did. That is a separate
  bug and is not fixed here.

The case for the change is therefore insurance rather than a demonstrated
repair: the model can now see the requirement it is being asked to write
against, on 99.7% of postings instead of 8%. That is worth 580 tokens.

## Consequences

- No wholesale re-enrichment. 620 postings are already enriched and the
  measured benefit of redoing them is a handful of rows; the cap matters
  going forward, on postings discovered from here.
- `jsa/posting.py` is the only place a description is sliced. A test asserts
  that no module in `jsa/` slices it itself, so the four-way drift cannot
  come back.
- Prompts are about 60% larger on the drafting path. Latency measured at
  19.7 s for a full-length posting on the largest model, against roughly
  12-15 s before.
