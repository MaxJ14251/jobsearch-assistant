# ADR 0001 — Compensation floor and work authorization

Status: accepted, amended 2026-09-15
Date: 2026-09-15
Amendment: decision 7 added. The original draft modelled compensation only as
a threshold, which silently meant "no floor" read as "pay plays no part in
ranking". That is not what most people want and was caught before publication.
Decided by: /software-design-council, chaired during the "make comp and work
authorization real scoring inputs" goal.

## Context

`job_search_preferences` carried four nulls since the profile was written:
`compensation_floor_usd`, `work_authorization`, `needs_visa_sponsorship`,
`willing_to_relocate`. Scoring ignored all four.

The goal that opened this ADR stated that a guard in the tailoring path
already failed loudly on these nulls. **That was not true.** Goal 01 specified
such a guard and it was never built — `grep` for `work_authorization` in
`jsa/tailor.py` returned nothing. Tailoring would have proceeded on a null
`work_authorization` and let the model decide what to say about it. The guard
was written as part of this ADR's work (see Consequences).

The goal that opened this ADR assumed the fix was to fill the fields in and
wire a compensation floor into ranking. Grounding the review in the actual
database changed that.

### Evidence gathered before deciding

- `jobs.salary_min` and `salary_max` are **100% NULL across all 514 rows**.
- **Nothing writes them.** Zero references to "salary" in `jsa/sources.py`
  (all 7 feed adapters), `jsa/enrich.py`, or `jsa/scoring.py`.
- **260 of 514 descriptions (50.6%) contain a dollar amount.** The data exists
  in the text, unextracted.
- Sampling those 260 shows they are mostly genuine pay ranges, but not
  uniformly: one sample was a *weekly lunch stipend of $75*, and several use
  multi-level structures ("Level I: $125,000-$145,000, Level II:
  $145,000-$200,000") where a single min/max is ambiguous.
- `degree_required` is true for **283 of 514** enriched rows and is
  deliberately **surfaced, not filtered**. `clearance_required` likewise.
  `years_required` *is* a hard reject. So the project already distinguishes
  facts that filter from facts that inform.

## Decisions

### 1. Scoring gets no salary term at all (not even an inert one)

The goal offered two paths: land the floor active, or land it "provably
inert". Both were rejected.

An inert floor is a dormant code path that silently activates the day salary
extraction lands. The top 20 shifts, and nobody diffs it, because the goal
that required the diff already closed. That is this project's signature
failure mode — a regex that could not match, a scanner that failed open, a
gitignore pattern matching nothing — rebuilt deliberately.

There is therefore **no salary term in `score_job`**. Not disabled, not
behind a flag: absent. Wiring one in becomes an explicit future change,
reviewed against real data at the time.

### 2. The floor is stored and validated, never null

Three states, distinguishable:

| Value | Meaning | `from_profile` | Tailoring |
|---|---|---|---|
| `null` / absent | Not yet decided | tolerated | **refuses** |
| `no_floor` | Deliberately no floor | valid | proceeds |
| a number, or a map keyed to `regions` | A floor | valid | proceeds |
| anything else | Malformed | **`ConfigError`** | n/a |

Where the error fires matters, and the first draft of this ADR got it wrong.
`Preferences.from_profile` feeds **discovery**, and the committed example
profile ships with these fields present but unset so a stranger can see what
to fill in. If `from_profile` raised on null, every fresh clone would fail at
`jsa discover` — and `tools/fresh_clone_check.py` copies that example verbatim
and follows the README, so the check would break too.

So the split is: `from_profile` validates **shape** and raises `ConfigError`
on a malformed value (a string that is not `no_floor`, a map with a key absent
from `regions`), but tolerates null and records it as undecided. The refusal
on null belongs **downstream in the tailoring path**, which is the only place
the value is actually needed and where Goal 01's guard already lives.

Null must still never silently read as "no constraint" — the two are
different, and the distinction is the entire point of the field.

The map form exists because a floor is often two numbers — the operator here
has a rent-free option in one region and a renting option in another. Keys
match the existing `regions` block, so they self-document:

```yaml
compensation_floor_usd:
  default: 95000
  pa: 85000
  sd: 110000
```

**This operator has set `no_floor`.** The map form is documented in the
example file for everyone else.

### 3. Work authorization is a surfaced fact, not a filter

Consistency decides this. `degree_required` covers 283 of 514 rows and does
not filter, precisely because a misread posting is invisible data loss. Comp
and sponsorship are read less reliably than degrees.

`needs_visa_sponsorship` is additionally a fact about the *candidate*, not the
posting. It cannot filter anything until a posting-side `sponsorship_offered`
fact exists, and none does.

### 4. Unknown salary is neutral

Not a penalty, not a filter. 100% unknown today and ~49% unknown even after
extraction lands. Penalising unknown punishes half the board for the
employer's disclosure policy. A role that does not post a salary is not a
worse role.

**The trap, recorded before anyone falls into it.** Once pay is a gradient
(decision 7), "neutral" means unknown must score the **midpoint of the pay
component, not zero**. Zero is the natural thing to write and it is wrong: it
would quietly sink roughly half the board for the employer's disclosure
policy, while looking like working code. Whoever implements the gradient must
assert this directly — score two otherwise-identical jobs, one with a posted
salary at the midpoint and one with none, and require the same total.

### 5. Drift is prevented mechanically, not by prose

`master_profile.example.yaml` is committed and copied verbatim by
`tools/fresh_clone_check.py`; the real profile is gitignored. They drift.

A test parses both and asserts their `job_search_preferences` key sets agree.
This follows the precedent in `tests/test_enrich.py`, which parses
`db/schema.sql` and asserts the seniority vocabulary matches the CHECK
constraint — added after those two drifted and killed an enrichment run
mid-pass on an IntegrityError.

### 6. The floor must never reach a prompt

`compensation_floor_usd` is salary-expectation data. `scrub_prompt` strips
`identity` fields; this lives under `job_search_preferences` and was outside
its scope. A test asserts these fields never appear in an outbound prompt.
NVIDIA's free tier logs prompts.

### 7. A floor is a threshold. Pay ranking is a gradient. They are separate.

The original draft of this ADR modelled compensation only as a floor, so
`no_floor` meant "compensation plays no part in ranking at all". Almost nobody
wants that. The operator's own words: *open to any offer between 70k and 160k,
but obviously the higher-paying job should rank higher.*

Those are two different mechanisms and only one of them is a setting:

| | What it does | Who needs it | Configurable |
|---|---|---|---|
| Floor | Excludes a job entirely | Rare — a hard budget constraint | **Yes**, optional |
| Pay ranking | Orders jobs by pay | **Everyone** | **No** |

`compensation_floor_usd` is therefore a **threshold only**. `no_floor` means
"no threshold" — it does not mean pay is irrelevant.

**Pay ranking is not a user setting**, and will not become one. `W_TITLE`,
`W_LOCATION`, `W_KEYWORDS` and `W_SENIORITY` are module constants that nobody
configures; a `W_COMPENSATION` alongside them is consistent rather than new. A
preference nobody disagrees with does not need a knob, and adding one is
speculative generality on a field every user has to read.

**Neither is implemented yet.** Both wait on salary extraction — see Tripwire.
When that lands, the work is: add `W_COMPENSATION` to the weight set,
rebalance the existing four so they still sum to 1.0, map unknown to the
midpoint (decision 4), and apply the floor, if one is set, as a hard reject
separate from the gradient.

## Tripwire

A test asserts `jobs.salary_min` is 100% null while the tracker holds rows.
**When that test fails, salary extraction has landed** and this ADR should be
revisited to decide whether to wire the floor in. It is a tripwire, not a
regression guard — it is meant to fail one day, loudly, in front of someone
who has to make a decision.

## Consequences

- `UndecidedPreferenceError` was **added** in `jsa/tailor.py`, implementing the
  guard Goal 01 specified but never built. It refuses to draft while
  `work_authorization` is null or blank, naming the field. The compensation
  floor is deliberately NOT on that required list: a floor governs which jobs
  to pursue, not what a resume says, and blocking document generation over it
  would fail for a reason unrelated to the document.
- Ranking is unchanged by this ADR. Both the floor and the pay gradient are
  decided here but deliberately unimplemented; see decision 7.
- `no_floor` is a statement about thresholds only. Setting it does not opt out
  of pay ranking, because pay ranking is not opt-in.
- Salary extraction is out of scope and non-trivial: multi-level ranges,
  stipends, hourly versus annual, currency.
