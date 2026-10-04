# ADR 0026 — Turbo mode decides interest, never submits

Status: accepted
Date: 2026-10-03
Relates to: ADR 0003 (application lifecycle), ADR 0012 (what applied
means), ADR 0015 (a map that agrees with its filter). Implements plan 16 of
the planning session.

## Context

The owner asked for a "Turbo mode" tab: one job at a time, swipe left if not
interested, right if interested, and a right swipe that "automatically
applies to the job for you".

## Decision

**Auto-apply is declined.** Two reasons:

1. It breaks the project's founding rule: "Human approves every application
   submission … nothing sends automatically. Not behind a flag, not a config
   value, not 'for testing'." SECURITY.md tells the public there is no code
   path that submits an application. A swipe decides interest; it is not a
   review of the documents that would go out.
2. No honest route exists. Greenhouse, Lever and Ashby accept applications
   through their APIs only with the employer's key; Workday needs a
   candidate account per tenant. Auto-submitting would mean scripting
   employer forms in a browser against their CAPTCHAs and bot checks.

**What the owner chose instead (2026-10-03): save and draft.**

- **Left = Pass.** Every copy of the posting (its `dedup_key` group) gets
  `jobs.passed_at` and leaves the deck and the Matches page. Nothing is
  deleted; rediscovery doesn't clear it; `jsa unpass` does, and `jsa passed`
  lists them. A pass isn't an application event: no application exists.
- **Right = Interested.** The job is saved (the person's act, `actor =
  human`) and a resume and cover letter are queued in `draft_queue`. One
  worker thread in `jsa serve` drafts them one at a time through
  `drafting.draft_document`, the same path as `jsa tailor`, so the same
  guards run and the drafts land in Review. `jsa/turbo.py` has no network
  code of its own; a test checks its imports.
- **Every failure is stored**, never raised: a failed item shows in the
  status strip with its one-line reason. Items a stopped server left running
  are re-queued on the next start.
- **A daily limit** (`TURBO_DAILY_JOBS = 20` jobs; `turbo.daily_jobs` in the
  profile overrides it; the busiest drafting day so far was 10 resumes).
  Past it a right swipe still saves but queues nothing, and nothing is
  queued for tomorrow on its own.
- **No unattended draft from a stub.** A posting with almost no text
  (`posting.thin`) is saved, not drafted. Plan 1 warns and still drafts when
  a person asks; an unattended draft from a stub is a guess nobody reviews
  as it is made.
- **Save only when drafting can't run**: an undecided profile or no API key
  is shown before swiping, and right swipes then save without queueing.
- **Undo.** Each swipe waits 5 seconds in the page before it is sent;
  undo inside that window writes nothing. Later, a pass is undone with
  `jsa unpass`; an Interested stays saved (applications are never deleted),
  and drafts not yet started can be cancelled from the status strip.

## Also fixed

- **Folding.** `v_new_matches` filtered before it folded copies of a posting,
  so saving the best copy brought the next one back as a "new" card. A group
  now leaves as a whole when any copy has an application or was passed, and
  the map's query uses the same rule (ADR 0015).
- **A model failure on `/job/{id}/tailor`** was a 500; it is now a message.

## Not done

Submitting applications in any form (that needs the owner to change the
founding rule, a new ADR and SECURITY.md), Turbo from a phone (the dashboard
binds to loopback), learning from swipes, and drafting tomorrow's
over-limit swipes automatically.
