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
  evaluated, and the text is read instead.
- The share of sales pay that is OTE was not measured.
