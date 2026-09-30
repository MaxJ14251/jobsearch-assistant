# ADR 0006 — Salary extraction and pay ranking

Status: accepted
Date: 2026-09-17
Amends: ADR 0001, decisions 1 and 7 and its Tripwire section. Decisions 2–6 of
0001 stand unchanged; decision 4 is implemented here exactly as written.
Decided by: /software-design-council, chaired during the "extract salary, then
decide deliberately what to do with it" goal.

## Context

ADR 0001 left `jobs.salary_min` and `salary_max` empty on purpose. It kept any
salary term out of scoring and set a tripwire test that would fail the day
extraction landed. It also set out what should happen then:

- the floor is an optional threshold;
- "higher pay ranks higher" applies to everyone;
- unknown pay scores the midpoint.

This ADR records that day.

### Evidence (514 postings in the live tracker)

- 286 descriptions contain a dollar figure. 234 contain a `$X - $Y` range.
- Of those 234: 69 state one distinct range, 157 state two, 8 state three.
  Two ranges is almost always SpaceX's "Level I / Level II" for one req.
  Three is one range per city.
- The rest are a mix. Funding rounds: "raised our $1.5B Series F". Benefits:
  "up to $20k in fertility services". Stipends: "a weekly lunch stipend of
  $75". A quota: "$1M+ annual quota".
- **The first sample misled us.** A sample of eight postings without a range
  showed only funding rounds, so the first design ignored lone figures.
  Reading all of them found 13 postings that state a wage as a single
  figure: "$30.00/hour", "Zone 1: $98,700 USD", "On Target Earnings:
  $125,000".
- Enrichment (`jsa/enrich.py`) sends the model only the first 6,000
  characters. Pay sits near the end of postings; Rocket Lab's is past
  character 9,000. Enrichment also runs after discovery has scored and
  filtered.

## Decisions

### 1. Extraction is a context-gated pattern, not the enrichment model

Both of the following are in `jsa/salary.py`.

**Ranges count** when both hold:

- pay wording appears in the 250 characters before the range ("pay",
  "salary", "base", "compensation", "OTE", "USD"...);
- no funding or perk wording appears within 45 characters ("raised",
  "Series", "stipend", "reimburse", "fertility", "401"...).

A range that closely follows an accepted range belongs to the same list. The
pay wording of a city-by-city list sits only at its top.

**Single figures** need stronger evidence, and are read only when a posting
has no usable range. One of these must hold:

- pay wording immediately before the figure;
- a unit immediately after it ("/hour", "per year").

A wider exclusion list also applies: "bonus", "commission", "variable",
"sign-on", "quota", "equity". Amounts written with M or B are never pay.

**The period follows the size of the figure.**

- A yearly figure must be between $20k and $1M.
- An hourly figure must be between $10 and $500, and must say "hour" nearby,
  because "$30 - $40" alone could be anything.
- When a posting gives both, the yearly figure wins.
- A range spanning more than 4× is refused.

**Why not the model:**

- It cannot see the pay: the text it receives is cut off before most pay
  ranges.
- It runs after scoring, so pay could never affect discovery.
- A full pass costs about 500 calls.

A regex cannot tell a stipend from a salary by itself; the context gate does
that work. The measured error rate is below.

**Option C, the pattern first with the model as a fallback, was rejected as
overengineering.** Nothing in the evidence justified it. The hand-check is
what would change that.

### 2. Postings with several ranges store the combined range

`salary_min` is the lowest minimum and `salary_max` is the highest maximum.

**Refusing to guess would drop 157 of 234 postings.** Matching levels to
seniority would need a mapping of each employer's level names to seniority,
and the evidence does not justify that.

The combined range is safe because of how it is used:

- **Ranking uses the minimum** (decision 4). A senior level at the top of a
  range cannot lift an entry-level match. The minimum is also the likeliest
  offer for this operator.
- **The floor uses the maximum** (decision 5). A job is excluded only if even
  its best case pays below the floor.

### 3. What is stored, and how a reader tells it apart

| Column | Meaning |
|---|---|
| `salary_min`, `salary_max` | Whole dollars in `salary_period` units |
| `salary_period` | `year` or `hour` (CHECK constraint) |
| `salary_text` | The words the figures were read from, plus "(+N more range(s) combined)" |

Rules:

- A missing `salary_min` with a missing `salary_text` means no figure was
  found. Nothing distinguishes "unstated" from "stated but unreadable". Both
  score neutral, so the distinction would change nothing.
- **Equity, bonus and "competitive" are not stored.**
- Hourly figures are stored as hourly, rounded to the dollar. `salary_text`
  keeps the exact wording.
- Hourly pay is converted to yearly (×2,080 hours) only when compared or
  ranked.
- OTE is stored as stated. `salary_text` shows the word "OTE" to anyone
  reading it.

### 4. Pay ranking: fit is scaled, not re-weighted

```
score = W_FIT × fit + W_COMPENSATION × pay        W_FIT = 0.90, W_COMPENSATION = 0.10
fit   = the unchanged four-part score × 0.7 on the sales track
pay   = linear from $50k (0) to $200k (1), on the annual minimum, clamped
      = 0.5 when unknown  — the MIDPOINT, per ADR 0001 decision 4
```

This amends ADR 0001 decision 7, which proposed rebalancing the four fit
weights. **Scaling the fit score instead means every job with unknown pay
keeps exactly its old order.** As a result:

- any movement in the ranking is caused by pay and nothing else;
- the before/after diff can therefore be fully explained (see Consequences).

**Keeping fit ahead of pay.** Pay moves a score by at most ±0.05 from an
unknown-pay job's score. For comparison:

- a full-versus-half title match is worth 0.20;
- the rejections for seniority, years of experience, location and people
  management run before pay is considered.

So a well-paid role the operator cannot realistically do does not outrank a
good fit. A test puts a $400k job with a weak title and a poor location
against a $50k job with a strong title and a good location, and requires the
$50k job to win.

**The sales discount applies to fit only.** The first implementation applied
it to the whole score, which also scaled the neutral pay term. Two sales and
engineering jobs, neither stating pay, could then swap places for no reason.
A test caught this before it reached the tracker.

The consequence, accepted: a sales job's pay is not discounted. An OTE figure
can add up to +0.05, the same as a base salary on an engineering role. The
0.7 fit discount still keeps sales roles below equivalent engineering roles.

### 5. The floor is a hard reject on the maximum, and never on unknown

When a floor is set:

- a job is rejected only if `salary_max`, converted to yearly, is below the
  floor for the job's region;
- the region is the first `regions` entry whose places appear in the
  location; otherwise the `default` floor applies;
- unknown pay is never rejected (ADR 0001 decision 4);
- the rejection reason gives both figures.

A floor changes nothing above it. A floor is a threshold; the gradient is
separate (ADR 0001 decision 7).

This operator has `no_floor`. The floor path is tested with synthetic
profiles: a flat floor, a floor per region, and hourly pay converted to
yearly on either side of the floor.

### 6. Where it runs

- **Discovery** extracts pay before scoring each fetched listing. `upsert_job`
  stores the result.
- **`jsa rescore`** re-extracts pay and re-scores every stored listing
  without polling any feed. It never deletes anything: a job that now scores
  below the discovery minimum keeps its row, with the new score and reasons.
  A tracker created before this change gains the two new columns on `jsa
  init`.
- **The floor never reaches a prompt.** Scoring makes no model call, and no
  code that builds a prompt reads the floor. This is asserted in
  `tests/test_preferences.py`.

## Tripwire, replaced

ADR 0001's tripwire failed on 2026-09-17, as designed: 252 of 514 postings
now carry pay. It is **replaced, not deleted**, by
`tests/test_salary.py::TestSalaryStaysExtracted`, which guards the opposite
direction. It fails when either of these happens:

- fewer than 95% of the postings whose text states pay have pay stored;
- more than 5% of stored pay disagrees with what the text says.

That catches extraction silently stopping: a feed change, a regex edit, or a
discovery path that skips the extractor. The "no salary term in scoring"
guard is retired with it; `TestPayIsWiredDeliberately` records why.

## Measured error rate (2026-09-17)

**Coverage.** 252 postings carry extracted pay: 240 yearly, 12 hourly. Of the
34 postings that mention money but carry none, every one was read in full:

- 33 are correctly empty (funding rounds, valuations, stipends, a quota);
- **1 is a miss**: "base salary range for this role is $143,00 to
  $210,000", which contains the posting's own typo.

**Missed postings: 1 of 253 that state pay (0.4%).**

**Accuracy, first sample.** 10 extractions at random, each compared against
every dollar figure in its source:

- 9 were exactly right;
- **1 was wrong**: job 507 lists three city ranges, and only two were
  combined. The minimum was right; the maximum was $144k instead of $164k.

**First-sample error rate: 1 in 10.** Fixed by the list rule in decision 1.

**Full audit of hourly and single-figure results.** All 21 were read.

- **4 were wrong**: "Zone 1 / Zone 2" hourly postings where Zone 2 sat too
  far from the word "hourly". The minimum was $29 instead of $27. Fixed by
  the same list rule.
- No non-pay figure was ever extracted, in any sample or in the audit.

**Accuracy, second sample** (a different seed, after both fixes): **10 of 10
correct**, including a "$20,000 differential" correctly ignored.

**Honest reading.**

- Before the fixes, 5 of the 31 hand-checked postings had a wrong figure
  (16%). Every one was a missing part of a list, never a false figure.
- After the fixes, no wrong figure is known.
- Only 20 random postings were sampled, so the true error rate is not zero,
  merely unmeasured below about 1 in 10.
- The sample is dominated by SpaceX postings, because the tracker is.

## Consequences

**Ranking.** The top 20 of `v_new_matches` was recorded before and after, on
copies of the tracker. Every position change is explained by pay alone:

- On all 514 jobs, the new score equals the fit-only score plus
  `0.10 × (pay − 0.5)`, with no exceptions.
- With pay forced to unknown, the stored order is reproduced exactly.

| Moved | Why |
|---|---|
| Anthropic 288: 11 → 2 | minimum $300k: +0.050 |
| Scale AI 392: 26 → 5 | minimum $180k: +0.037 |
| Notion 461: 36 → 13 | minimum $180k: +0.037 |
| Scale AI 393: 76 → 15 | minimum $216k: +0.050 |
| Notion 466: 21 → 14 | minimum $135k: +0.007 |
| Tebra 279: 3 → 17 | minimum $80.5k: −0.030 |
| T. Rowe Price 505: 5 → 11 | minimum $97k: −0.019 |
| Baseten 428, 444, 449: 16–18 → 21–23 | pay unstated, so no change; overtaken by the four above |
| Notion intern 464: 20 → 25 | $57/hr, about $118.5k: −0.004 |
| SpaceX 29, 213: unchanged | minimum exactly $125k, the neutral point |

**Other effects.**

- A job's score can now cross the discovery cutoff (0.35) because of pay
  alone. This was accepted: new listings are scored the same way, and nothing
  already stored is deleted.
- The $50k–$200k band is a US figure, consistent with ADR 0002.
- Some job-board feeds may publish structured pay fields. Those were not
  evaluated, and the text is read instead. (Now evaluated: see "Structured
  pay fields" below.)
- The share of sales pay that is OTE was not measured.

## Structured pay fields (2026-09-28, n22)

The last bullet above is now evaluated. Two of the three big boards return pay
as data, but only when asked, and this project never asked. Each call now asks,
on the same host with one more parameter:

| Board | Parameter | Field | Shape |
|---|---|---|---|
| Ashby | `includeCompensation=true` | `compensation` | Tiers of components: `compensationType` (Salary, EquityCashValue, Bonus…), `interval` ("1 YEAR", "1 HOUR", "1 MONTH"), `currencyCode`, `minValue`, `maxValue` |
| Greenhouse | `pay_transparency=true` | `pay_input_ranges` | `min_cents`, `max_cents`, `currency_type`, `title`; **no interval** |
| Lever | none (always sent) | `salaryRange` | `min`, `max`, `currency`, `interval` ("per-year-salary", "per-hour-wage", "per-month-salary") |

### Decisions

1. **The same rules as the text.** `salary.from_ashby`, `from_greenhouse` and
   `from_lever` return the same `Salary` as `extract`, or `None`:
   - base pay only;
   - a year or an hour only (a month is unknown, never multiplied);
   - `ANNUAL_BOUNDS` and `HOURLY_BOUNDS` apply;
   - several ranges are combined (decision 2), and dollars and a year win over
     other currencies and an hour.
   Greenhouse gives no interval, so the period comes from the range's title
   ("hour" means hourly) or from its size (≥ $20k means yearly). An
   hourly-sized figure whose title does not say "hour" is refused, exactly as
   in text.
2. **The board's field wins over the text.** It is the employer's declared
   range. The text is the fallback. `salary_source` records which one was
   used (`field` or `text`). `rescore` re-reads text figures but never
   overwrites a field figure with a text guess.
3. **Currency is stored and compared only with itself.** `salary_currency`
   holds it and `salary_text` shows it ("CAD 35–38/hr"). The floor, the pay
   ranking and the dashboard's analytics treat a non-USD figure as unknown:
   neutral, never rejected, and never averaged into dollar figures.
4. **No backfill command.** `discover` updates stored postings as it
   re-fetches them, so a single run filled the tracker.

### Measured (open postings, one discover run)

| Source | Before | After | From field | From text |
|---|---|---|---|---|
| greenhouse | 78% of 787 | 78% of 790 | 220 | 398 |
| ashby | 19% of 463 | **97%** of 574 | 474 | 86 |
| lever | 0% of 59 | **27%** of 60 | 16 | 0 |
| themuse | 70% of 80 | 70% of 80 | — | 56 |
| workday | 83% of 79 | 83% of 80 | — | 67 |
| rss | 45% of 22 | 47% of 23 | — | 11 |
| custom | 19% of 21 | 19% of 21 | — | 4 |
| **all** | **56%** (844 of 1,512) | **81%** (1,332 of 1,629) | 710 | 622 |

- Dashboard cards with pay rose from 729 of 1,336 to 1,206 of 1,452.
- **No posting lost pay.** Of the 878 that had a figure before, 30 changed:
  29 are now read from the field, and 1 (SpaceX) changed its own posting.
- Stored field periods: 684 year, 26 hour. Stored currencies: 1,329 USD and
  3 CAD. (Most non-USD postings are filtered out as outside the US before
  pay is read.)
- Across every configured board, including postings that were filtered out:
  - 2 Ashby postings and 1 Lever posting gave a monthly interval (refused);
  - 4 Ashby postings carried a non-base component with figures (ignored);
  - 159 Greenhouse ranges had a non-base title such as OTE (skipped);
  - 108 Greenhouse fields were refused: 94 were hourly-sized with no "hour"
    in the title (Rocket Lab, Vast), and 14 were placeholder figures such as
    $1–$2.

**Error rate.** 30 field figures (14 Ashby, 12 Greenhouse, 4 Lever) were
compared with the posting page in a browser: **30 agree, 0 disagree.** Where a
posting has both a field and a figure in its text (227 postings), they are
identical or within 5% in 200. The 27 that differ:

- **23 Rocket Lab: the field is right and the text parser was wrong.** The
  text reads "Total Compensation (base and equity) $X–$Y … Base Salary
  $A–$B", and the parser combined both ranges, stretching the maximum to
  include equity. This is a text-parser gap against decision 1's equity
  veto. The field masks it for these postings, and it is harmless to the
  floor, which only errs toward keeping a job.
  **Fixed 2026-09-28.** A range is now refused when the label directly
  before it says total compensation, or base and equity, stock or bonus
  (`_TOTAL_LABEL`). "Equity" alone does not count, so "eligible for
  equity. The base salary is …" still reads the base, and OTE is still
  stored as stated. Re-run over every posting in the tracker, this changed
  25 postings, all Rocket Lab, each to its base range. None lost pay.
- **3 OpenAI: the company's field and prose disagree.** The field is the
  figure OpenAI's own page shows at the top (checked in a browser); the prose
  further down gives another range. The field wins, per decision 2.
- **1 Vast: the field names one level, the prose two.** The field gives only
  the senior level's range. The maximum, which the floor uses, is the same.

### Refused on purpose (step e)

Of the 33 Greenhouse postings with a dollar figure but no stored pay:

- 3 are Anthropic weekly stipends, correctly refused.
- 1 is a CoreWeave typo ("$143,00"), correctly refused.
- 29 are Vast and Rocket Lab ranges such as "Pay Range: California
  $28–$46 USD". They are refused because an hourly-sized figure must say
  "hour" (decision 1).
- 31 open SpaceX postings have the same shape ("Level 1: $26.00 – $32.00").

These roughly 60 postings are probably hourly. Accepting hourly figures by
size alone would loosen a refusal this ADR made on purpose, so that is left to
the owner. It was not changed here.

## Hourly figures that never say "hour" (2026-09-29, n25)

The refusal above, left to the owner, came back to them with numbers:
69 open postings were refused only because an hourly-sized range never
said "hour". They were 35 SpaceX, 27 Vast, 3 Illumina, 2 Rocket Lab and
one each from Manpower and Flexport. Every figure fell between $15 and
$80.

**The owner's decision:** accept such a range when there is evidence it is
hourly beyond its size. Either:

- **it is written to the cent** (`$23.00`), because salaries are not quoted
  to the cent; or
- **the posting says the role is paid hourly**: "a non-exempt position",
  "eligible for overtime pay", or a shift differential.

Size alone is still not enough. The same rule applies to single figures.

**The wording had to be narrower than first proposed.** The first version
counted "non-exempt" or "overtime" anywhere in the posting. Measured
against the 1,311 open postings with annual pay:

- "non-exempt" appears in 39 of them, because Vast's benefits paragraph
  offers "vacation for non-exempt staff" in every posting;
- "willingness to work overtime" appears in 18 salaried Vast postings.

Neither says anything about the role in front of you. The phrasings kept
appear in none of the salaried postings.

**Measured, re-reading every posting in the tracker:**

- 62 gained hourly pay: 31 SpaceX, 26 Vast, 3 Illumina, 1 Manpower and
  1 Flexport. 51 were accepted on cents and 11 on "non-exempt position".
- 1 existing figure changed, correctly. ServiceTitan's Zone 2
  ("$19.61 USD - $29.42 USD") had been dropped because "hourly" was too far
  above it; the combined range is now $20–$31 an hour.
- 0 postings lost pay, and no annual figure changed.
- 7 stay refused, as unknown pay, which is neutral and never rejected:
  - 4 with no second signal ("Level 1: $33 - $39", "Base Pay Range
    (CA Only) $22-$27 USD");
  - 3 that had only boilerplate (a fixed-term recruiter at Vast, two SpaceX
    welder postings).
- **Hand check:** all 11 wording-only postings plus 19 accepted on cents,
  30 in all. Every one is an hourly-type role (technicians, inspectors,
  coordinators, drivers, security officers). Agree: 30, disagree: 0.

**Where it applies.** The board-field path (`from_greenhouse`) is
unchanged: a pay field has no text to read wording from. When it refuses
an hourly-sized field, discovery falls back to the text, which now
applies this rule. Stored figures update on the next `jsa rescore` or
`jsa discover`.
