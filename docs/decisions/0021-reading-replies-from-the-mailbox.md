# ADR 0021 — Reading replies from the mailbox

Status: accepted
Date: 2026-10-02
Relates to: ADR 0003 (application lifecycle), ADR 0004 (outreach never
sends), ADR 0011 (feedback triggers nothing). Implements plan 6 of the
planning session.

## Context

Replies to applications (rejections, interview requests, receipts) arrive by
email, among thousands of others. Every stage change waited for the operator
to find the email by hand and run `jsa status`.

The owner chose, on 2026-10-02:

- a live, **read-only** link to Gmail over IMAP, with a Gmail **app
  password** in `.env`;
- **the tool suggests; the owner confirms.** This is the rule the code
  already followed for "ghosted": a report, never a status change.

## Decision

`jsa/inbox.py` reads the mailbox and stores **suggestions**; a person
confirms or dismisses each (`jsa inbox`, or "Replies from your email" on the
dashboard's Pipeline page).

1. **Read-only, structurally.** It is the only module that imports imaplib.
   The folder is opened with `readonly=True` (IMAP EXAMINE), every fetch is a
   `BODY.PEEK`, so nothing is marked read, and the only IMAP commands in the
   module are LOGIN, SELECT, UID SEARCH, UID FETCH and LOGOUT. No SMTP
   exists anywhere in `jsa/`. `tests/test_inbox.py` holds all of this, from
   the source and from the commands a fake server records. A deliberate
   `imap.store(` was added once to prove the guard fails, then removed.
2. **No model, and no mail kept.** Matching and classification are fixed
   rules run locally; nothing from a message goes to a model. A stored reply
   keeps its subject, date, sender domain and the NAME of the rule that
   matched. There is no body column.
3. **Suggest, never decide.** Fetching never writes to `applications`.
   Confirming calls `approvals.set_stage()`, the same path as the
   dashboard's stage form, which records the change as the human's, with the
   email's date and subject in the note.
4. **What it looks for.** Applications in `applied` or a later live stage.
   One Gmail search (`X-GM-RAW`): the hiring systems that mail on employers'
   behalf (Greenhouse, Lever, Ashby, Workday, iCIMS, SmartRecruiters,
   Jobvite) or any applied employer's name, since the day before the
   earliest application. A message counts when the employer is named in the
   sender's name or domain, or the subject; in the body only when a hiring
   system sent it ("Vast" is also a word). Two applications at one employer
   are told apart by the job title; otherwise the reply is stored as unclear
   and the owner picks.
5. **Classification**, first match wins: rejection, then offer, then an
   interview invitation, then a receipt. Rejection goes first because
   rejections usually also say "thank you for applying". An invitation needs
   a phrase that asks for time ("your availability", "schedule a call", a
   scheduling link), not the bare word "interview", which receipts use too.
   Rejection suggests `rejected`, an offer `offer`, an invitation the next
   interview stage; a receipt suggests nothing.
6. **Secret hygiene.** The scanner refuses the app-password setting with a
   value in any file but `.env` (writing that example out here tripped it), and an app password's shape on a line that says
   "password". `jsa doctor` checks that `.env` is git-ignored and the commit
   hook is on, and says whether the mailbox is set up, never with what.

## Precision

**Not yet measured on a real mailbox.** The plan's step 3: once the owner has
put an app password in `.env`, run one read-only fetch, have the owner mark
every suggestion right or wrong, tune only the phrase lists, and record the
COUNTS here (never subjects, senders or text).

## Later uses

- `jsa outcomes` (plan 13) counts a reply linked to an application, other
  than an automatic receipt or a dismissed one, as "heard back". It reads;
  it changes nothing.
- A confirmed interview reply (plan 14) suggests interview prep for that
  round and runs nothing: no prep is drafted and no next action is set
  until the person asks.

## Rejected

- **Automatic stage changes**, including "ghosted": the tool does not decide
  what happened to an application.
- **OAuth / the Gmail API**: it needs a Google Cloud project. Revisit only if
  app passwords become unavailable (Google still offers them in 2026, for
  personal accounts with 2-Step Verification).
- **A model to classify mail**: it would send the operator's mail to a
  third party for a job a phrase list does.
- **Running on a schedule**: it runs when the owner asks.

## Note, 2026-10-03

The owner may schedule `jsa daily` (ADR 0028), which runs this among its steps. The tool itself still never creates or runs a schedule.

## Note, 2026-10-23

A real "Thank you for applying to … at …" receipt was read as an interview:
its body described what comes next, in words the interview rule matches.
The owner had set the application to rejected from the next day's email, so
nothing moved. Now a receipt's **subject** wins over the softer interview
phrases (scheduling a call, "your availability", a phone screen, next steps
in the hiring process). A rejection, an offer, a booking link (Calendly,
GoodTime) or "invite you to an interview" still wins over the subject.
Receipts, and any reply with no stage to suggest, get "Got it" on the new
Replies page: noted, no stage moves.

## Precision measured, 2026-10-25 (G6)

Every stored reply (11) was found again in the mailbox by its Message-ID and
re-read the way `jsa inbox` reads (folder read-only, BODY.PEEK, nothing
marked read), then classified in memory with today's rules
(`tools/measure_inbox.py`; email text is never written or printed). The
owner labelled the same 11 on a local page, blind to the tool's reading:
8 labelled, 3 "not sure".

| | right, of the 8 labelled |
|---|---|
| readings stored when fetched | 7 |
| today's rules | **8** |

All 8 labelled were receipts. The one miss was the receipt read as an
interview, fixed by the receipt-subject rule (2026-10-23); no rule changed
for this measurement. Of the 3 not labelled, two were read as "other" and
one as a rejection, which the owner had acted on as one (2026-10-13).

The sample is small and holds no real interview or offer yet, so it says
nothing about those rules beyond the receipt case. Re-run the tool after
the first of each.
