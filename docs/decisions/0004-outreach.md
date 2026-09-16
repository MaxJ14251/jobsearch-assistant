# ADR 0004 — Outreach: reachable, never sendable

Status: accepted
Date: 2026-09-16
Decided by: /software-design-council, chaired during the "wire outreach"
goal.

## Context

`jsa/outreach.py` (207 lines, 16 tests) was imported by zero modules; contacts
and outreach were empty. The brief's hardest line governs here: *human approves
every outreach message; nothing sends automatically.*

Grounding the review in the code settled most of it, and more favourably than
the questions assumed.

### What was already true

- **`draft()` already queues through the one approval path.** It calls
  `approvals.queue(con, "outreach", ...)`, and `mark_sent()` refuses unless
  `approvals.is_approved(con, "outreach", id)`. ADR 0003 decision 5 is already
  satisfied; it only needed locking with a test.
- **Only three contact fields reach the prompt** — name, title, company. Email
  and LinkedIn URL never leave the database. That was already right.
- **`draft()` already verifies and limits** before inserting: banned terms,
  degree phrasing, and the channel character limit.

## Decisions

### 1. The verb is `mark-sent`, not `sent`

`jsa outreach sent 3` reads as an imperative — *send this* — and it is the
exact opposite. The command records something the tool cannot observe and will
never do.

`jsa outreach mark-sent 3` cannot be misread. The cost of the longer name is
five characters; the cost of the shorter one is a reader believing this tool
transmits messages.

The state machine itself is confirmed as the schema already had it:
`drafted → approved → sent`, with `sent_at` set only by the human, after they
sent it themselves, elsewhere.

### 2. `mark-sent` requires an approval. `jsa applied` does not. That asymmetry is correct

It looks inconsistent with ADR 0003 decision 4 and is not.

An `outreach` row exists **only because this tool drafted it**. Approving it
costs one command, and a message going out unapproved is the precise thing the
brief forbids.

An `application` can exist for a job you applied to by hand with a resume this
tool never made. Refusing to record that would make the tracker wrong.

The difference is whether the artefact originated here.

### 3. Contact data is tiered, because a referral ask that cannot name its recipient is not outreach

| Field | Stored | May reach a prompt |
|---|---|---|
| name, title, company | yes | **yes** — personalisation is the point |
| email, LinkedIn URL | yes | **never** — contact mechanics, not message content |

Every other privacy guard in this project protects the operator. This is the
first that protects **someone else**, and the asymmetry matters: the operator
consented to running the tool; the contact did not.

`tools/scan_secrets.py` gains an email-address rule for committed files. The
risk is not the database — it is gitignored — but a test fixture or example
containing a real person. Measured before adding it: exactly one real-looking
address exists anywhere in the repository, in the gitignored profile, so the
rule lands with zero false positives.

### 4. Channel limits stay, as dated data rather than doctrine

`LINKEDIN_CONNECT_LIMIT = 300` is not scope creep. The tool drafts *for a
channel*; a 300-character message that the channel silently truncates wastes
the operator's time in a way they discover only after sending.

But a platform's UI limit is not a fact about this tool, so it lives in
`CHANNEL_LIMITS` as data with a dated comment, and the error names where the
number came from.

## Consequences

- The no-send guarantee is proven across the whole transitive import graph,
  not the top-level file the previous test walked.
- `jsa contact add` stores a third party's details. The README says plainly
  what of that reaches a model and what does not.
