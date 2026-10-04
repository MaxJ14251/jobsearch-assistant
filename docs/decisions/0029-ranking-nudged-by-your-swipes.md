# ADR 0029 — Ranking nudged by your own swipes

Status: accepted
Date: 2026-10-04
Relates to: ADR 0015 (a map that agrees with its filter), ADR 0026 (Turbo).
Implements plan 19 of the planning session.

## Context

Turbo records a pass (`jobs.passed_at` on the posting's group) and a save
(an application). The owner wants matches ordered by what they actually
like. Scoring is a fixed weighted sum stored in `jobs.match_score` with
readable reasons.

## Decision

1. **A nudge, not a new score.** `jsa/learning.py` learns, per feature
   (role kind, title words seen at least 3 times, company, source kind,
   remote or not, each enriched stack item), a smoothed save rate
   `(saves+1)/(saves+passes+2)` against the overall one. Each feature
   contributes at most 0.05, the total at most **±0.10**, and a feature
   counts only after 5 observations. No model; nothing leaves the machine.
2. **Nothing until 30 swipes.** Below `MIN_SIGNALS = 30` the order is
   byte-identical to before. On 2026-10-04 the owner had 12 (12 saves, 0
   passes), so nothing changes yet.
3. **One place, three uses.** `learning.rank` runs after the matches query
   and before the radius and per-company cap, for Matches, Turbo and `jsa
   matches`. It reorders in memory: `match_score` is never written, the set
   of matches is unchanged (ADR 0015 holds), and a hard-rejected job (score
   0) is never in the list to begin with. The view's choice of which copy
   represents a group still uses the stored score.
4. **Explained.** A nudged card says why, e.g. "+0.04: you saved 6 of 7
   'support' roles". `jsa learned` lists the active features, and Turbo
   shows the swipe count.
5. **Resettable, and off if wanted.** "Reset learning" (Turbo, or `jsa
   learned --reset`) records a time; swipes before it are ignored and
   nothing is deleted. `ranking.learn_from_swipes: false` in the profile
   turns it off. An unpassed job stops counting.

## Measured

On a copy of the tracker: today, 12 swipes, the top 20 are identical. With
40 synthetic swipes added (20 saves of support titles, 20 passes of others,
all from below the top 100), 19 of the top 20 changed position and 10 new
cards entered it. That is the bound working on a packed list, not breaking
it: stored scores run 0.93 at #1, 0.85 at #20 and 0.83 at #60, every
entrant scored at least 0.807, and the largest nudge among them was +0.055.

## Not done

Model- or embedding-based ranking, changing stored scores, filtering by
swipes, and learning from approvals, rejections or outcomes (too few).
