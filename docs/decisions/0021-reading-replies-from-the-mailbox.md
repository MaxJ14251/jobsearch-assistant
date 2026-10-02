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

## Rejected

- **Automatic stage changes**, including "ghosted": the tool does not decide
  what happened to an application.
- **OAuth / the Gmail API**: it needs a Google Cloud project. Revisit only if
  app passwords become unavailable (Google still offers them in 2026, for
  personal accounts with 2-Step Verification).
- **A model to classify mail**: it would send the operator's mail to a
  third party for a job a phrase list does.
- **Running on a schedule**: it runs when the owner asks.
