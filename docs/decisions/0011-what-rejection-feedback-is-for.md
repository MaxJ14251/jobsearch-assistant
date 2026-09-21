# ADR 0011 — What rejection feedback is for

Status: accepted
Date: 2026-09-20
Relates to: ADR 0003 (application lifecycle), ADR 0005 (selection).

## Context

`jsa reject` requires feedback. The dashboard asks for it. Twelve rejections
accumulated in the author's tracker, every one with a written reason, and
nothing in the codebase read any of them.

Worse, the tool had once been described — out loud, by the agent working on
it — as learning from that feedback. It never did. The wording in
`approvals.reject` and the README was corrected at the time. This ADR settles
the question the correction left open: if feedback does not train anything,
what is it for?

## What the twelve rejections actually say

```
5x  "contains claims not in the profile"
4x  "superseded by a later version"
2x  "no work history; keep the most recent job"
1x  "use the promotion bullet for work history"
```

Three different kinds of thing, and only one of them is judgement.

### The five: a verifier miss, since closed

Every one of those drafts passed `verify_draft` and was then rejected by a
human for claiming things the profile does not support. All five were re-read
off disk and checked against today's guards:

| draft | what the human saw | caught today by |
|---|---|---|
| doc 6 | profile says "**Goal**: reduce a multi-hour workflow"; resume said "**Reduced** a multi-hour workflow" | `claims_completion`, and `added_share` 0.22 |
| doc 5 | summary invented "strict **SLA adherence**", "rapid incident response", "cross-functional escalation" | `added_share` 0.41 against a 0.15 ceiling |
| doc 4 | "implementing automated content analysis for **production** workflows" on a project marked *in development* | `added_share` 0.22, `claims_completion` |
| doc 3 | "Designed", "Integrated", "Built" where the profile says "Designing", "Integrating", "Building" | `claims_completion` |
| doc 1 | installer bullets printed under **both** "Sales Representative" and "Installer" | **no text guard could — fixed at the cause, below** |

Four of five are now caught by work done after those rejections:
`added_share`/`INVENTION_CEILING` and `claims_completion`. The human was ahead
of the verifier, the verifier caught up, and this is the record of it.

### The fifth is a different defect entirely

Doc 1 fabricated nothing. Every sentence traced to a real profile bullet, which
is exactly why `verify_draft` passed it. What was wrong was *placement*: two
bullets belonging to the Installer role were printed under the Sales
Representative heading as well, and none of the Sales Representative's own
bullets appeared. The resume claimed installer work *as a sales rep*.

Root cause, reproduced: `render` groups bullets by
`exp.get("id") or exp.get("company")`. An experience entry with no `id:` falls
back to the company name, so **two roles at the same employer collapse into one
bucket** and every bullet prints under both headings. Being promoted without
changing employer is common, and the example profile a newcomer copies has
exactly that shape.

The three text guards all operate on text versus its source text. None of them
can see that a faithful sentence is under the wrong heading, so this is fixed
at the cause rather than by adding a fourth guard: the parent key now falls
back to the company **and title** together, which is what distinguishes the two
roles. An explicit `id:` still wins.

A previous change had already noticed this and made the key prefer `id:`,
which fixed it for a profile that has ids. `id:` is not required, so the
fallback stayed broken for exactly the newcomer the example profile invites —
somebody promoted without changing employer.

### The four: bookkeeping dressed as judgement

"Superseded by a later version" is not a decision. v1 stops needing one the
moment v2 exists, and the tool knows that at the moment it writes v2. Six of
the eight still-pending approvals were the same thing.

### The three: the loop that already worked

The work-history rejections became the `keep_work_history` rule and the
`work_history_bullet` setting — read by a person, who changed the tool. That is
the loop, and it is not being replaced.

## Decision

**Feedback is a record for a person, and a trigger for nothing.**

1. **Superseding is automatic.** `approvals.supersede_older` closes pending
   approvals for earlier versions when a new one is written, with
   `decision='superseded'` and `decided_by='tool'`.
2. **The audit trail is enforced in both directions, by triggers, not
   convention.** The existing trigger already refused an `approved` or
   `rejected` row not signed `human`. A second one refuses a `superseded` row
   that *is* signed `human`. A person cannot file a supersede; the tool cannot
   sign a decision. `superseded` is not `approved`, so nothing outbound can
   key off it.
3. **Prior feedback is read back to the person drafting the next version**, by
   `jsa tailor`, at the moment it would change what they do. It was previously
   visible only on a dashboard page nobody had open.
4. **Feedback never reaches a model.** It is the operator's free text, it can
   contain anything including their own address or something prompt-shaped,
   and this tool does not tune itself on it. A test walks every module in
   `jsa/` and fails if the word `feedback` appears on a line that also builds
   a prompt or calls the model.

## Rejected alternatives

- **Feed prior feedback into the redraft prompt.** The obvious move, and the
  reason not to is the one this project keeps relearning: it makes the tool
  appear to learn while actually laundering untrusted free text into a prompt.
  If it is ever done, it needs scrubbing, an injection test, and its own ADR.
- **Score or classify feedback.** No. Twelve notes is not a dataset, and a
  classifier would be the "gets better as you use it" claim by another route.
- **Let a human keep rejecting superseded drafts.** That is what produced six
  stale queue entries and four rejections that say nothing about quality.

## Consequences

- The pending queue now reflects decisions actually outstanding.
- `jsa tailor` prints what it closed and why, so a shrinking queue is never
  mistaken for somebody having decided something.
- Existing databases are migrated: SQLite cannot alter a CHECK constraint, so
  `_rebuild_approvals_if_stale` now also rebuilds when the decision CHECK
  lacks `superseded`.
- The six superseded approvals already in the author's tracker are **not**
  touched by this change. They close on the next redraft of each document, or
  by an explicit backfill the operator asks for. Nothing rewrites their
  history of decisions without being asked.
- The placement defect is closed at the cause. A *guard* for it does not
  exist: nothing checks which heading a bullet is rendered under, and a future
  way to produce the same defect would not be caught. Worth knowing before
  trusting the three text guards to mean "this document is honest".
