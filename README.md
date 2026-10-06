# Agentic Job Search Assistant

[![tests](https://github.com/MaxJ14251/jobsearch-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/MaxJ14251/jobsearch-assistant/actions/workflows/ci.yml)

Pulls job listings from 50 company career systems, filters thousands of postings
(9,725 on 2026-09-29) down to a reviewable shortlist, extracts the disqualifying facts buried in the prose, and hands
you the final call on every application.

**United States only.** Non-US locations are excluded outright, not ranked low — so
if you are job hunting outside the US, this will return nothing and the emptiness
will not explain itself. That scope is deliberate; see
[docs/decisions/0002-geographic-scope.md](docs/decisions/0002-geographic-scope.md).

**What you get depends on where you live, and on one free key.** Fifty of the
fifty-one shipped feeds are a single employer's own job board, so between them
they cover the places those employers have offices and nowhere else. Measured
2026-09-26 over 1,147 collected postings: **25 states had no on-site posting at
all**, and in-state on-site counts ran 364 for California and 0 for Idaho.

The fifty-first feed is the fix. It asks one job-site's public API about **your
own cities** across many employers, so it works wherever you live. Measured the
same day, per discovery run of 100 postings a city:

| Your city | Local postings | Remote | Employers |
|---|---|---|---|
| Boise, ID | 9 | 91 | 17 |
| Columbus, OH | 57 | 43 | 21 |
| Seattle, WA | 73 | 38 | 18 |
| Los Angeles, CA | 79 | 30 | 21 |

Boise's number from the other fifty feeds is zero.

It needs a free key in `.env` as `MUSE_API_KEY` (register at
[themuse.com/developers/api/v2/apps](https://www.themuse.com/developers/api/v2/apps)
— their terms ask you to). Without it that source is skipped, discovery still
runs, and you get remote roles plus whatever the employer feeds hold near you.
`jsa doctor` tells you your own numbers on day one. You can still add employers
near you to [config/companies.yaml](config/companies.yaml) and run
`python -m jsa verify`; see
[ADR 0009](docs/decisions/0009-what-the-shipped-feed-list-is.md) for why the
employer list is not simply made longer, and
[ADR 0013](docs/decisions/0013-one-nationwide-source.md) for why one
aggregating source was the answer instead.

## Quickstart

```bash
git clone https://github.com/MaxJ14251/jobsearch-assistant && cd jobsearch-assistant
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt
cp .env.example .env                                        # add your API key
cp profile/master_profile.example.yaml profile/master_profile.yaml
.venv/Scripts/python -m jsa init
.venv/Scripts/python -m jsa discover --min 0.5
.venv/Scripts/python -m jsa matches --limit 20
```

Edit `profile/master_profile.yaml` with your own history and target roles first —
everything downstream reads from it. On macOS and Linux use `.venv/bin/python`.

```
[0.90] #421 T. Rowe Price — Full Stack Software Engineer, AI Lab
       New York, NY; Baltimore, MD (onsite)
       ⚑ asks for a degree  |  2+ yrs
       stack: Java, Python, JavaScript, AWS, Azure
       note: BS or MS required or equivalent experience accepted
       · title matches 'AI Engineer' (100%)
       · located in Baltimore, MD
```

**Every command in this README runs from the project folder.** `.venv/...` is a
path relative to it, so from anywhere else, including a fresh PowerShell window
that opens in `C:\WINDOWS\System32`, it fails with "is not recognized". Go there
first. In Windows PowerShell, `&&` does not work either, so run the two halves
of those lines separately, and write the path with backslashes:

```powershell
cd "C:\path\to\jobsearch-assistant"
.\.venv\Scripts\python -m jsa serve
```

## Status

| # | Module | State |
|---|--------|-------|
| 1 | Master profile | **Done** — [master_profile.example.yaml](profile/master_profile.example.yaml) |
| 2 | Tracker DB schema | **Done** — [db/schema.sql](db/schema.sql), validated on SQLite |
| 3 | Job discovery | **Done** — 50 verified feeds; national employers plus AI labs |
| 3b | LLM enrichment | **Done** — degree/clearance/stack facts the filter can't see |
| 4 | Resume/cover tailoring | **Done** — verifier, scrubber, ATS-safe .docx, provenance |
| 5 | Review dashboard | **Done** — `jsa serve`, loopback only |
| 6 | Interview prep | **Done** — degree/gap drills |
| 6b | Outreach | **Refuses more often than it drafts** — see below |

## Setup

Needs Python 3.11, 3.12 or 3.13. From this directory:

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt
```

## Your first hour

Eight steps, in order. Only the last three cost API calls; everything before
them runs on your machine for free.

**1. Make the tracker** (a SQLite file in this directory):

```bash
.venv/Scripts/python -m jsa init
```

**2. Copy the example profile and fill it in.** This file is the whole point:
every sentence in every document comes out of it, and the tool will not write a
claim that is not in it.

```bash
cp profile/master_profile.example.yaml profile/master_profile.yaml
```

Your name and contact details, what you have actually done as short bullets,
the titles and places you want. It is gitignored, so it stays on your machine.
Budget most of the hour here; everything downstream is only as good as this.

Or skip the YAML: start the dashboard (`.venv/Scripts/python -m jsa serve`;
it creates the tracker if there isn't one) and follow **Setup** at
http://127.0.0.1:8765/setup. Import your resume or start from the example,
fill in what you're looking for in a form, see what's still missing, make
it your profile (first run only; an existing profile is never overwritten),
and run the first search in the background.

Or start from your resume, as .docx or a text PDF:

```bash
.venv/Scripts/python -m jsa import-resume my_resume.docx
```

It writes `profile/master_profile.draft.yaml`, never your real profile. Every
bullet, employer and school in it is copied word for word from the resume;
tags, target titles and families are marked `# suggested: check`, and the
decisions only you can make (work authorization, pay floor, your degree as you
would state it) are left as TODO. A PDF is read locally; a scanned one (no text
layer) is refused, and if its layout loses much of the text, for example two
columns, it says so and suggests the .docx. It prints what is missing and
what the draft would match. Review it, then copy it to `profile/master_profile.yaml`. Your
name and contact details are removed before the resume's text goes to the
model (one call; it needs the API key, set up in step 6).

**3. Ask what is still missing:**

```bash
.venv/Scripts/python -m jsa doctor
```

It names every field that will stop the tool working, says what to write
instead, and changes nothing. Exits 0 when you are ready. Run it again after
edits.

**4. Find jobs** (no API key needed; about 13 minutes, because it asks each
board politely, one request at a time):

```bash
.venv/Scripts/python -m jsa discover
```

**5. Read what it found:**

```bash
.venv/Scripts/python -m jsa matches --limit 20
```

Each match shows its score with the reasons behind it — title, location,
keywords, seniority, pay — so you can see when the tool is wrong.

To find roles by name, search titles: `--search "support engineer, data analyst"`,
or the search box on the dashboard. Commas mean "or"; each word must start a
word in the title, so `ai` finds "AI Engineer" but not "Maintenance".

**6. Add your API key.** Steps 7 and 8 call a language model; the rest never
does. Copy the example and put your key in it:

```bash
cp .env.example .env
```

`.env` holds `NVIDIA_API_KEY=...`. Get one free at build.nvidia.com. The key
never leaves that file, and your name, address, phone and email are never sent
to the model — a check refuses the request if they appear in a prompt.

**7. Save a job and draft for it** (one model call each). The number is the
one `matches` prints after the score, `#421` in the example above; yours will
differ:

```bash
.venv/Scripts/python -m jsa save 421
.venv/Scripts/python -m jsa tailor 421
.venv/Scripts/python -m jsa tailor 421 --kind cover-letter
```

**8. Read the draft, decide, and apply yourself:**

```bash
.venv/Scripts/python -m jsa review          # what is waiting on you
.venv/Scripts/python -m jsa review 1        # read one in full
.venv/Scripts/python -m jsa approve 1
.venv/Scripts/python -m jsa applied 421     # after YOU submit it
```

Or do the same in a browser with `jsa serve`.

**What it will not do.** It does not submit applications, send email, or
message anyone — there is no code path that transmits. It will not write a
claim your profile does not make. It does not search every employer: it polls
the boards listed in `config/companies.yaml`, and you add your own. If you set
it up, it reads your mailbox only when you ask, and never writes to it.

**Replies from your email (optional).** With a Gmail app password in `.env`
(see `.env.example`), `jsa inbox` or "Check email" on the dashboard's Pipeline
page reads your mailbox read-only and finds replies to your applications:
rejections, interview requests, offers, receipts. It suggests a stage change
for each, and nothing moves until you confirm it (`jsa inbox confirm <id>`)
or dismiss it. Nothing is marked read, nothing is sent, and no mail text
goes to a model. See
[ADR 0021](docs/decisions/0021-reading-replies-from-the-mailbox.md).

**Where the shipped feeds point.** `config/companies.yaml` ships 50 employer and
aggregator feeds (listed under [Feed coverage](#feed-coverage)). Their on-site
jobs sit where those employers have offices, mostly California, Washington and
Texas. Measured from a profile based in Columbus, Ohio: the matches that came
back were almost entirely remote roles, because those companies post few jobs
in Ohio. If you
are not in a coastal tech hub, add local employers' boards to that file —
`jsa verify` tells you which tokens actually work — or lean on remote.

## Usage

### Every day: `jsa daily`

```bash
.venv/Scripts/python -m jsa daily
```

One run of the routine work: a verified backup, discovery, then the inbox
check (if you set it up). A failed step doesn't stop the next, and the
command exits 1 if any failed. It ends with what's new since the last run
(new matches with the best five, replies to confirm, follow-ups due, drafts
waiting in Review), the dashboard shows the same as a banner until you click
"Got it", and a UTF-8 log goes to `logs/` beside the tracker.

To have it run every morning, print the command and run it yourself; the
tool never schedules anything:

```bash
.venv/Scripts/python -m jsa daily --schedule-help --at 07:00
```

On Windows that prints a `schtasks /Create ...` line for Task Scheduler. See
[ADR 0028](docs/decisions/0028-a-daily-run-the-owner-schedules.md).

### Backups

The tracker is the only record of your applications, so copy it:

```bash
.venv/Scripts/python -m jsa backup            # a verified, dated copy in backups/
.venv/Scripts/python -m jsa backup --list
.venv/Scripts/python -m jsa backup --check    # are the files you sent still unchanged?
```

A copy holds the tracker (taken with SQLite's online backup, so it is safe
while the dashboard runs), `output/` and your profile, with a manifest of
row counts and file hashes; `.env` is never copied. The newest 10 manual
copies are kept. `--to DIR` writes somewhere else, such as another disk;
the copy is as private as the tracker. A copy is also taken automatically
just before an upgrade rebuilds a table, and if it can't be made the
upgrade doesn't run. `jsa doctor` mentions a copy older than a week.

To go back to a copy, stop the dashboard, then:

```bash
.venv/Scripts/python -m jsa restore backups/2026-10-02_153000-manual        # the tracker only
.venv/Scripts/python -m jsa restore backups/2026-10-02_153000-manual --all  # and output/, profile
```

It verifies the copy, asks you to type `restore`, and copies the current
state first (a `pre-restore` copy). See
[ADR 0025](docs/decisions/0025-backing-up-the-tracker.md).

```bash
.venv/Scripts/python -m jsa doctor
```
Reads your profile and tracker and reports what will not work yet, in plain
language: unanswered fields that make drafting refuse, placeholder text left
from the example, bullets with no tags, education entries with no credential
line, tags that match none of the postings you have collected, and whether an
API key is configured (never what it is). It changes nothing, and exits 1 while
something still blocks you — so it also works as a setup check in a script.

```bash
.venv/Scripts/python -m jsa verify
```
Probes every feed in [config/companies.yaml](config/companies.yaml) and reports what
actually returns listings. Run this after editing that file — board tokens are not
derivable from a company name, and a wrong one either 404s or silently returns someone
else's board.

```bash
.venv/Scripts/python -m jsa discover --min 0.5
```
Polls verified feeds, reads each posting's pay, scores every listing, and writes
keepers to the tracker. Listings that vanish from a feed are marked closed rather
than deleted.

```bash
.venv/Scripts/python -m jsa matches --limit 20
```
Shows unreviewed matches, best first, each with the reasons behind its score,
including what it pays. `--near <region>` restricts to commuting range of a region
you define under `job_search_preferences.regions`; `--per-company` (default 3)
stops one large board from filling the page — SpaceX alone posts 2,373 reqs.

**How pay counts.** Most postings state pay. Where the job board publishes it
as data (Ashby, Greenhouse, Lever), that figure is used; otherwise
`jsa/salary.py` reads it from the text (ADR 0006). Only dollars are compared
with dollars: pay in another currency is stored and shown, and otherwise
treated as unstated. Higher pay ranks higher for everyone, on the *bottom*
of the stated range, and moves a score by at most ±0.05 — enough to order
similar roles, never enough to lift a poor fit over a good one. A posting that
states no pay scores exactly as if it paid the middle of the band, so saying
nothing is not a penalty. If you set `compensation_floor_usd`, a job whose
*top* figure is below it is rejected, with the reason shown; unknown pay is
never rejected.

```bash
.venv/Scripts/python -m jsa rescore
```
Re-reads pay from the stored text (a figure from the board's own pay data is
kept as it is) and re-scores every stored listing without polling any feed. Run it
after changing your profile's preferences.

```bash
.venv/Scripts/python -m jsa enrich --min 0.5 --limit 600
```
Runs a small LLM over listings that survived the free filter and records the facts
regex cannot reach — whether a degree is required, whether a clearance is needed, the
real years of experience, and the actual tech stack. **Nothing is rejected**; the flags
appear in `matches` so the human decides.

```bash
.venv/Scripts/python -m jsa tailor 421 [--kind cover-letter] [--force]
```
Drafts an ATS-safe .docx from your profile bullets, records full provenance, and
queues it for your approval. The job must be saved first.

**The resume report.** Each draft ends with a short report on what your
*profile* could add: a job shown with one bullet, bullets with no figures, a
gap of more than six months with nothing in it, a project whose every bullet
is still in progress, soft traits listed as skills, filler in the summary, a
certification whose name reads as a code, a project's stack your skills don't
list. It says what is missing and where to add it, never what to claim, and
it never blocks a draft or an approval. Run it on its own with
`jsa coach` (`--strict` exits 1 on any finding, if you want a gate); it is
also shown on the review page beside Approve. The word lists live in
`config/coach.yaml`. See [ADR 0023](docs/decisions/0023-the-resume-report.md).

When you reject a draft, you can add a reason code beside your note
(`jsa reject 12 --feedback "..." --reason wrong_bullets`, or the dropdown on
the review page). You pick it; nothing classifies your feedback. After a
`wrong_bullets` rejection, the next draft prints last time's bullets beside
the new ones. See [ADR 0024](docs/decisions/0024-reason-codes-on-rejection.md).

**Tailoring is mostly selection.** The tool picks which of your bullets fit this
role and which section leads, based on what the role *is*: the title decides
whether it is an engineering, support or sales role, and the command prints
which one it chose so you can dispute it. The bullet text itself changes very
little, and that is deliberate.

Every rewrite is checked in both directions. It must keep enough of your
original, and it must not **add** anything your profile does not say — an
outcome, a skill, a scenario, a quality. A bullet that adds one reverts to your
own words, and the command prints why. Work you describe as ongoing
("Building…", "Goal: reduce…") may not be rewritten as finished. A posting
demanding Kubernetes gets a gap report, not a Kubernetes bullet.

**Cover letters are written, not pasted.** `--kind cover-letter` used to
concatenate your summary and three bullets: every sentence true, and not a
letter -- no greeting, no closing, nothing saying what you were applying for.
A letter is now drafted by the model and then held to one rule (ADR 0007):
every word must come from your profile, from a fixed list of ordinary
connective words, or from the role title and company, which are values in your
tracker. **The posting's wording is deliberately not allowed**, so the letter
cannot borrow the employer's demands or praise the company. On top of that it
may never mention a degree, may never describe your ongoing work as finished,
and may never use a term on your do-not-claim list. If a letter fails those
checks twice, you get one composed from your own verified sentences, and both
the command and the review page say so rather than passing it off as prose.

The second check exists because the first one was not enough. It measured only
what survived, so a rewrite that kept your whole bullet and appended "during
high-priority support scenarios" scored *higher*. Replayed over every document
written before the fix, it reverted 13 of 19 rewrites.

One limit worth knowing, down from two. The model reads the first **12,000**
characters of a posting, which covers 99.7% of this tracker completely; it
used to read 4,000, at which point 92% of postings were cut and one in five
that stated a degree requirement stated it where the model could not see it.
The command still says so when it truncates, and now quotes the words it
stopped at so you can find the boundary in the posting. One function decides
this for every model call — see
[ADR 0016](docs/decisions/0016-how-much-of-a-posting-the-model-reads.md), which
is also honest about how much the change actually fixed. And a redraft needs
`--force`, producing a new version, a new file and a new pending approval; the
old one is never overwritten, because approval is per version.

```bash
.venv/Scripts/python -m jsa contact-add --name "..." --company Replit --title "..."
.venv/Scripts/python -m jsa outreach-draft --contact 1 --job 421 --channel linkedin_connect
.venv/Scripts/python -m jsa outreach-show 1
.venv/Scripts/python -m jsa outreach-mark-sent 1
```
Drafts networking messages. **Nothing here can send.** That is proven, not
**Outreach usually refuses, and that is the honest state of it.** Measured over
nine real drafts — three postings, three channels — it produced one message and
declined eight. It declines when the model reaches for a word that nothing in
your profile, the posting, or the recipient's record supports, because a
referral ask is read by a person who may check.

Two things are true at once. The drafts it produces are ones a person would
send. And the check that certifies them is a candidate-vocabulary check
borrowed from cover letters, where it works because a letter is almost entirely
about the candidate — while an outreach message is substantially about the
recipient, their company and the role, so it has no way to tell "the platform
is reliable" (a claim about their product) from "I am reliable" (a claim about
you). It fails closed. See
[ADR 0010](docs/decisions/0010-outreach-verification.md).

In practice: expect to write these yourself, and use the tool's refusal as a
list of the claims you would have had to stand behind. The failure message
names the words.

The one thing that has always held: nothing is transmitted, and it is not
promised: a test walks the whole transitive import graph of `jsa.outreach` and
asserts no sending library appears anywhere in it. The only network-capable
module in that graph is the LLM client, and every POST it makes goes to the
configured chat endpoint.

The last command is called `mark-sent`, not `sent`, because it records
something **you** did elsewhere and the shorter name reads like an instruction
to send. It refuses unless a human approved the message first.

A contact's name and title reach the model — a referral ask that cannot name
its recipient is not outreach. Their email and LinkedIn URL never do.

LinkedIn caps connection notes at 300 characters. Models cannot count
characters: measured over ten real drafts with the limit stated plainly in the
prompt, all ten came back 432–556. Handing the measured length back and asking
for a cut fixed it — 8 of 10 then landed inside the limit, none over. The
other 2 fail honestly and tell you to run it again.

```bash
.venv/Scripts/python -m jsa prep 3 [--round technical]
.venv/Scripts/python -m jsa prep-show 5
```
Interview questions drawn from that specific posting, each citing the line that
prompts it, plus the drills that lead every set: the degree, and the
employment gap when your profile's dates show one. **57% of matched postings
state a degree requirement**, so the degree answer is built from your
profile's `credential` line, verbatim: a degree you hold is stated as you
wrote it, and coursework stays coursework. It is checked against a
deny-list, verb-family patterns, and a negation window — "I did not finish
the degree" passes; "after I graduated" does not. The gap answer is built
from your own dates and what your profile records since. Answer notes
are first person, so they read as words you can say rather than a briefing about
you. Running again appends; prior prep is never overwritten.

```bash
.venv/Scripts/python -m jsa answers 421 [--regenerate]
```
Copy-ready answers for a saved job's application form, also on the job page
with a Copy button each. **Facts come straight from your profile**, with no
model: work authorization (verbatim), sponsorship and relocation, work
arrangement, your links, the education line, years of experience (summed
from your own dates). Anything undecided says so instead of guessing. There
is **no salary answer**: it shows the posting's range, and your floor never
leaves the profile. **Why this role** and **a relevant project** are drafted
from your bullets in one model call and held to the cover letter's checks;
one that fails is built from your own sentences instead, and says so. **Why
this company** is always built from your own sentences. Nothing is submitted
and nothing needs approval: you copy them in. See
[ADR 0027](docs/decisions/0027-application-answers.md).

When you move an application to *phone screen*, *technical* or *onsite*
(with `jsa status`, the stage form, or by confirming an email reply), the tool
says so and names the command: `jsa prep 12 --round phone_screen`, or the
prep you already have for that round. It runs nothing and sets no next action.
On the dashboard the job page then shows a **Draft interview prep** button
with the round picked; pressing it is the request, one model call, like
Tailor.

```bash
.venv/Scripts/python -m jsa tags
```
Shows which of your profile's bullet tags actually appear in the postings you
have collected. A tag that matches nothing is dead weight: selection can never
use it. Hyphenated dead tags fall back to their meaningful words
(`customer-facing` matches "customer"); the rest are listed for you to reword
in your own profile. The command never edits the profile.

```bash
.venv/Scripts/python -m jsa save 421
```
Tracks a match as an application. This is deliberately explicit: a job you never
pursued is not an application, and auto-creating one whenever you draft something
would fill the pipeline with noise. One application per job is enforced by the
database, so running it twice is safe.

```bash
.venv/Scripts/python -m jsa review          # everything awaiting your decision
.venv/Scripts/python -m jsa review 7        # one item, in full
.venv/Scripts/python -m jsa approve 7 --note "looks right"
.venv/Scripts/python -m jsa reject 7 --feedback "lead with the support work"
```
The approval gate. **These commands, and the matching web routes, are the only
ways a decision reaches `decided_by='human'`** — and the database refuses any
other write, so it is a constraint rather than a convention. Feedback on a
rejection is required and kept as the record of why. It is not yet fed back
into the next draft; to change what future drafts say, edit your profile. A
decision is per document version and is never inherited by a redraft.

```bash
.venv/Scripts/python -m jsa applied 421 --check     # see what it would record
.venv/Scripts/python -m jsa applied 421             # after YOU submit it
.venv/Scripts/python -m jsa applied 389 --resume 15 --no-cover
.venv/Scripts/python -m jsa applied 512 --via linkedin   # where you applied
```
Records that **you** submitted it — the tool cannot observe that and never will —
and **exactly which documents went out**, fixed at that moment. Drafting a new
version afterwards does not move it, and the database refuses an edit to it, so
the day before an interview `jsa prep` drills the resume the interviewer is
holding rather than whatever was drafted last.

By default it records the one resume and one cover letter you approved. A draft
you did not approve is never assumed sent; it is named back to you, and you add
it with `--resume DOC` / `--cover DOC` if it did go out. With two approved
versions of one kind it refuses and asks which, because a permanent record is
not written on a guess. Something missing can be added later; something recorded
cannot be changed. It also records where you applied (`linkedin`, `indeed`,
`employer` or `other`): read from the job's link unless you pass `--via`, so
`jsa outcomes --by via` can compare channels once there are enough
applications. It still does not *require* an approval: the gate stops the
agent acting on its own, not you applying with a resume you wrote by hand. See
[ADR 0012](docs/decisions/0012-what-applied-means.md).

```bash
.venv/Scripts/python -m jsa status 421 phone_screen --note "30 min with the hiring manager"
.venv/Scripts/python -m jsa next 421 "send the follow-up" --due 2026-10-01
.venv/Scripts/python -m jsa due --days 7
```
The rest of the journey. `status` moves an application to any stage the schema
knows — saved, drafting, ready, applied, phone_screen, technical, onsite, offer,
rejected, withdrawn, ghosted — and refuses anything else, listing the real ones.
Every move is recorded as **yours**: the tool cannot observe a phone screen and
never claims one happened. `next` records what you intend to do and when, which
is an intention rather than an event, so it does not change the status. `due`
reports what is overdue, due soon, or has had no activity for three weeks. It
changes nothing, and it never decides you were ghosted — that stays a stage you
set yourself.

Closing an application (rejected, withdrawn, ghosted) takes it out of the live
pipeline and keeps its whole history.

```bash
.venv/Scripts/python -m jsa outcomes
.venv/Scripts/python -m jsa outcomes --by source_kind   # or role_kind, cover_letter, redrafted, speed, via
```
What happened to each application you sent: the furthest point it reached,
*applied*, *heard back* (a reply from the employer, a rejection, or any later
stage; an automatic "we received your application" doesn't count),
*interview* or *offer*, with *closed* and *quiet* (no activity for three weeks)
beside it. A stage you undid doesn't count. `--by` gives plain counts per
group; `source_kind` means the source that found the job first, since later
finds aren't recorded. A group under 10 applications shows counts only, "too
few to compare": a rate on four applications is luck. The same list is under
"What happened" on the dashboard's Pipeline page. It recommends nothing and
changes nothing.

```bash
.venv/Scripts/python -m jsa add https://job-boards.greenhouse.io/company/jobs/1234567
.venv/Scripts/python -m jsa add --paste --company "Acme" --title "Support Engineer" --file posting.txt
```
Adds one job discovery did not find, and on the dashboard **Add a job** does
the same. A Greenhouse, Lever, Ashby or Workday link is read from that
board's public job API, through the same code discovery uses; the page itself
is never fetched, and the tool only calls an API address it built from the
board and posting id, so a link cannot send it anywhere else. Any other link,
LinkedIn and Indeed included, is refused without being fetched, and you paste
the posting text instead. It is stored whatever it scores, because you chose
it, with a note when your filters would have dropped it. If it is already in
the tracker you get the same row back. One model call reads the posting for
degree, clearance and years (`--no-enrich` skips it); nothing about you goes
with it.

```bash
.venv/Scripts/python -m jsa find "Perplexity AI" "Support Engineer" --city "Austin, TX"
```
Seen a job on LinkedIn or Indeed? Type what the listing shows, and this looks
for the same job where the tool is allowed to look: your tracker, the
employer's board from `config/companies.yaml` (checked live, for postings
newer than your last discovery), or, for an employer with none, a few guessed
Greenhouse, Lever and Ashby boards (at most 9 requests). The answer is
**same job** (same title and city, from your tracker or a configured board),
**possible** (a close title, another city, no city given, or anything from a
guessed board; check it), or **not found** (apply on the listing site). It
only ever requests those boards' public APIs. A listing link you give is
kept as text and never opened. A guessed board that answers prints a
`companies.yaml` entry for you to add and `jsa verify`; nothing writes that
file for you. The dashboard's **Add a job** page has the same search ("Seen
it on LinkedIn or Indeed?"). See
[ADR 0030](docs/decisions/0030-finding-a-listing-on-the-employers-board.md).

```bash
.venv/Scripts/python -m jsa serve
```
Local review dashboard on http://127.0.0.1:8765. It covers the whole path from a
match to a resume you can upload yourself:

- **Turbo** (`/turbo`): one match at a time, with the same filters as
  Matches. **Pass** (← key, drag left, or the button) takes the posting and
  its copies out of your matches (`jsa unpass <#>` brings it back; `jsa
  passed` lists them). **Interested** (→, drag right) saves the job and
  drafts a resume and cover letter in the background into Review, up to 20
  jobs a day (`turbo: {daily_jobs: N}` in the profile). Each swipe can be
  undone for 5 seconds (Z). A posting with almost no text is saved, not
  drafted. **Nothing is ever submitted:** you approve the drafts and apply
  on the employer's site, then `jsa applied`. Auto-applying was asked for
  and declined; [ADR 0026](docs/decisions/0026-turbo-decides-interest-never-submits.md)
- **Learning from your swipes.** After 30 swipes (passes plus saves), the
  order of Matches, Turbo and `jsa matches` is nudged by what you have
  favoured or passed: at most ±0.10 per match, so it reorders near-ties and
  never lifts a weak match over a strong one. A nudged card says why ("+0.04:
  you saved 6 of 7 'support' roles"). Stored scores never change and nothing
  is hidden. `jsa learned` shows what it learned; "Reset learning" on Turbo or
  `jsa learned --reset` starts over (nothing is deleted);
  `ranking: {learn_from_swipes: false}` in your profile turns it off. See
  [ADR 0029](docs/decisions/0029-ranking-nudged-by-your-swipes.md).
  says why.
- **Import your resume** (`/import`, linked from *Add a job*, and from
  Matches when there is no profile yet): drop a .docx or PDF on the page and get the
  same draft and report as `jsa import-resume`. It writes only the draft, and
  the uploaded file is read in a temporary folder and deleted.
- **Matches**, filtered by **"within N miles of here"** — a ZIP or a city and
  a slider in miles, pre-filled from your profile, or *anywhere in the US* for
  a nationwide search. Each card shows how far away the job is. Remote roles
  always pass a radius, because a radius is not what decides them; the page
  says how many of the results are actually near you, how many the radius hid,
  and how many of those name a place it could not find — unknown is not the
  same as far.
- **A map**, drawn on your machine from the shipped outline: your radius as a
  circle, one bubble per town sized by how many jobs are in it, and the ones
  just outside drawn faintly so you can see what widening the slider would
  reach. Toggle *anywhere* and it becomes the lower 48. There are no map
  tiles: a tile layer would tell a map server where you live, one request per
  tile, every time the page loads. A posting that is remote, unplaceable or
  off the edge is counted under the map in words rather than dropped, and a
  dot is inside the circle exactly when the list says the job is in range —
  the picture is measured first and drawn second, so it cannot disagree with
  the filter. See
  [ADR 0015](docs/decisions/0015-a-map-that-agrees-with-its-filter.md).
- **Live, with JavaScript on** (it works without; this is what it adds): the
  map sits beside the list and pans and zooms. The circle and a count of the
  postings inside it follow the slider as you drag, and the list refreshes
  from the server when you let go. Markers cluster when they overlap and are
  coloured by status: your saved, applied and interviewing jobs appear
  alongside new matches, with chips to show or hide each. Hovering a card
  rings its markers; clicking one moves the map to it. Clicking a numbered
  bubble lists exactly the postings in it, even ones the radius or the
  per-company cap had left out, until you go back to the whole list. Shaded bands show
  **estimated** commute times by car, transit, bike or on foot, and every card
  gets one too. They are straight-line distance times a typical detour at an
  average speed, not a route, and the page says so. Below the map and list,
  **area analytics** cover what the list shows: pay (only for postings that
  state it, and it says how many do), the skills asked for, who is hiring,
  and the spread of commute times. Under it all is a real map: coast, lakes,
  built-up areas, county lines, freeways and the names of the bigger cities. It is drawn from public Census
  data that ships with the tool, and placed by the same calculation as the
  markers, so a marker in a town sits on that town. Past the zoom at which
  that data is accurate (about a tenth of a mile), the map fades out and says
  so. Zoomed out to the country, the map turns to match the usual map of
  the US, and a north arrow shows by how much; zoomed in, north is straight up. Nothing is loaded from any other host. See
  [ADR 0018](docs/decisions/0018-the-matches-page-as-a-live-map.md) and
  [ADR 0019](docs/decisions/0019-a-basemap-from-shipped-public-data.md).
- A **Pipeline** grouped by stage, with each application's next action,
  overdue flags, and the same stage moves as the CLI.
- **Job page**: every drafted document, newest version first, with its status
  (pending, approved, rejected and your note), model, the profile bullets it
  used, the keyword gaps it could not honestly fill, a **Preview** that shows
  the .docx as a page (read from the file itself, so it is what you would
  send) and a **Download .docx** link. Its interview preps are listed below. A **Save** button starts an
  application; a **Tailor** button runs exactly what `jsa tailor --force`
  runs and queues the draft for review.
- **Apply by hand** (on a job you saved but haven't applied to): a checklist
  in the order an application form asks. (1) Where to apply: the listing's
  link, or "Apply on the employer's site instead" when your tracker has the
  same job from the employer's own board. (2) The approved resume with its
  Download link, or a note that none is approved, plus a warning when a newer
  draft is waiting in Review. (3) The cover letter, marked optional. (4) The
  copy-ready application answers. (5) An **I applied** button that records
  today's date, where you applied (LinkedIn, Indeed, the employer's site or
  another site, read from the job's link and changeable), and exactly which
  files you sent: the same record `jsa applied` writes. Recording an
  application without an approved resume asks you to tick a box first. The
  tool submits nothing. LinkedIn and Indeed both take .docx uploads (checked
  2026-10-05).
- **Interview prep** page: each question, why it is asked, and your
  first-person answer notes.
- **Review queue**: each draft line beside the profile bullet it came from,
  with any word your profile does not support highlighted, plus the whole
  draft. Approve/Reject call the same functions as the CLI.

Downloads are served only from `output/`, after resolving the path, and only
`.docx` files. The dashboard answers only requests addressed to a loopback
name, and every form carries a per-process token, so another site open in
the same browser can neither read your documents nor approve anything.

```bash
.venv/Scripts/python tools/fresh_clone_check.py
```
Simulates a fresh clone, greps for personal data, then follows the README literally.
Any step where you have to deviate from your own instructions is a bug in them.

```bash
.venv/Scripts/python -m jsa env      # is the API key wired up?
.venv/Scripts/python -m jsa models   # which models can this account call?
.venv/Scripts/python -m unittest discover -s tests -t .
```

## Design notes

**The master profile is the only source of truth for claims about the candidate.** The tailoring
module selects and rewords existing bullets by tag; it never invents experience. Fields
marked `TODO` in the profile are unanswered — a tailoring run fails loudly rather than
guessing. `ats_keywords.aspirational_do_not_claim` lists skills that must never appear
on a resume until they are actually true.

## Why there are two tiers, and where the line sits

The free deterministic filter takes ~6,900 listings to ~500. Only then does a model
run, over the survivors. Measured on 50 real postings:

| | deterministic filter | LLM enrichment |
|---|---|---|
| Cost | free | ~1,300 tokens per listing |
| Speed | seconds for 6,900 | ~2.3s per listing |
| Sees | titles, locations, keywords | prose: degree, clearance, real stack |

Running the model over all 6,900 would be ~9M tokens and hours per run, to reproduce a
decision a regex already makes correctly. Running it over the 500 survivors costs ~14x
less and adds what the regex structurally cannot see: **29 of 50 sampled listings
required a degree**, and 4 required a clearance named nowhere in the title.

### The model failures this design assumes

Both are real, observed on 2026-09-14, and both are silent:

* `nemotron-3.5-lightning` is a **reasoning model**. Left with thinking enabled it
  spends its whole token budget thinking and never answers — 2% parse rate, 73s
  median. With `enable_thinking: False`: **98% parse rate, 2.5s median.**
* With thinking on it echoed the prompt's own schema, and a naive
  first-brace-to-last-brace JSON scan returned `["up to 5 primary technologies"]`
  **as if it were extracted data.** Well-formed, confident, wrong.

So: `extract_json` takes the *last* balanced object rather than the first, rejects an
array where an object was requested, and every enrichment field is type-checked before
it reaches the database. A value that fails validation becomes NULL — meaning
"unknown", never "no".

### The fabrication guard

Generated text is never trusted. Every bullet must map to a bullet id in
`master_profile.yaml`, must retain at least 45% of its source's meaningful words
(below that it has drifted into a new claim), and may contain nothing from
`ats_keywords.aspirational_do_not_claim`.

This is not theoretical. Asked to tailor against a posting demanding Kubernetes,
PyTorch and a PhD, `nemotron-3-ultra-550b` produced, unguarded:

> "Architected and operated a multi-region **Kubernetes** model-serving platform,
> developing custom Go controllers... Implemented **PyTorch** FSDP/DeepSpeed
> distributed training pipelines, cutting training time for 70B+ parameter models
> by 35%... **Holds a PhD in Computer Science** with dissertation focus on
> distributed systems optimization; **8+ years** production experience."

Every claim false, down to invented metrics. Through `tailor()`, the same model
and posting produced only traceable bullets and reported all eight demanded
technologies as gaps. Reproduce it with `python tools/fabrication_demo.py`;
the offline regression tests live in `tests/test_tailor.py`.

One boundary bug worth noting, found by these tests: `\bC\+\+\b` never matches,
because a word boundary cannot sit between `+` and a space. C++ was silently
undetectable as a banned claim, and so were C#, F# and .NET.

**Scoring is deliberately not an LLM call.** Running a model over thousands of listings
is slow, expensive, and unauditable. The filter is a transparent weighted score — title
match 45%, location 25%, keyword overlap 20%, seniority 10% — and every point is
attributed to a reason string stored in `jobs.match_reasons` and shown in the CLI. The
model's job starts at tailoring, where judgment actually earns its cost.

**Hard rejects** (score 0, regardless of everything else): a senior/staff/principal/lead
title, a people-management title, a required security clearance, a stated requirement
above 3 years of experience, or a location outside the US.

"Manager" is handled by position, not presence: as the head noun ("Manager, Software
Engineering") it's a people-management job and rejected; trailing ("Technical Account
Manager") it's an IC role and a target.

**Regional copies collapse; different jobs do not.** Boards post one req per
location — "Forward Deployed Engineer (Korea)", "(West Coast)". Those share a
`dedup_key`, and the dashboard shows the best-scoring one with a count of the
rest. A qualifier folds only when it says *where*: a region, a state, or the
posting's own town. It used to fold every parenthetical, and SpaceX puts the
team there — eighteen different "Software Engineer (Starlink / Platform Team /
AI Data Engineering…)" jobs were one card, and 139 titles were hidden that way.
Place names alone cannot decide it ("Falcon" is a rocket and a town in
Colorado), so the posting's own location does. With a radius set, a folded card
counts as near you if *any* of its copies is. See
[ADR 0017](docs/decisions/0017-what-folds-into-one-card.md).

**Feeds are public JSON only** — the same endpoints each company's own careers page
already calls: Greenhouse `boards-api`, Lever `api.lever.co`, Ashby `posting-api`,
Workday `wday/cxs`, Workable `widget/accounts`, two company-specific APIs, plus RSS.
Descriptive User-Agent, delay between requests, no auth, no scraping behind a login.
Playwright is deliberately absent from requirements.txt.

**Nothing is hard-deleted.** Rows carry `archived_at`.

## Feed coverage

`config/companies.yaml` holds 55 sources: 50 employer feeds and aggregators,
the nationwide source, and four employers kept for reference with no public
feed. They are grouped below by the kind of employer. Where a company is
headquartered is not where its jobs are: SpaceX's own board supplies
postings in Washington and Texas as well as California, and Shield AI's in
Washington.

Measured on 2026-09-29 over 9,725 postings from the 50 employer and
aggregator feeds that answered. The nationwide source is left out, because it is
asked about the reader's own cities.

| kind of employer | feeds | postings | remote | WA | OH | ID | CA | TX | NY |
|---|---|---|---|---|---|---|---|---|---|
| AI labs & dev tools | 14 | 2,986 | 1,156 | 326 | 4 | 0 | 1,163 | 43 | 573 |
| Aerospace, hardware & vehicles | 6 | 4,101 | 64 | 573 | 4 | 2 | 1,440 | 784 | 20 |
| Health & life sciences | 8 | 487 | 73 | 0 | 1 | 1 | 56 | 2 | 4 |
| Software & consumer | 17 | 1,749 | 174 | 93 | 10 | 0 | 461 | 48 | 134 |
| Finance & industrial | 2 | 337 | 0 | 3 | 2 | 0 | 6 | 9 | 7 |
| Remote aggregators | 3 | 65 | 52 | 0 | 0 | 0 | 0 | 0 | 0 |

Coverage follows large multi-site employers, not where a feed's company sits.
That is why Washington and Texas get in-state roles nobody configured, and
Ohio and Idaho get almost none.

**The list is deliberately not grown region by region.** One regional group
showed what that costs: 45 regional employers probed by hand, one usable
feed found, 4% of the postings, and almost nothing for anyone outside that
area. `matches --near <region>` filters to the commute areas your own profile
names, and `matches --remote` covers the roles that work from anywhere. Full
reasoning in [ADR 0009](docs/decisions/0009-what-the-shipped-feed-list-is.md).

*AI labs & dev tools:* Anthropic, OpenAI, Scale AI, Perplexity, Sierra, Cohere,
LangChain, Replit, Baseten, Modal, Vercel, Notion, Hugging Face, CoreWeave.

*Aerospace, hardware & vehicles:* SpaceX, Rocket Lab, Vast, Shield AI,
Airspace, Rivian.

*Health & life sciences:* Illumina, Element Biosciences, Geisinger, Highmark
Health, Becton Dickinson, GoodRx, Sidecar Health, Tebra.

*Software & consumer:* ZipRecruiter, Snap, System1, Riot Games, Scopely,
ServiceTitan, GoGuardian, Boulevard, Match Group, Tealium, Kyriba, Mitek,
GoFundMe, ClickUp, Gopuff, Duolingo, Petco.

*Finance & industrial:* T. Rowe Price, Johnson Controls.

*Remote aggregators:* We Work Remotely, Hacker News jobs, Python.org.

Four of these needed work beyond a board token:

| Company | ATS | What it took |
|---|---|---|
| GoodRx | Workday | Site is `careers`, not `GoodRx`. Needs a detail request per job — the listing response says only "2 Locations". |
| ServiceTitan | Workday | `total` comes back only on page 1 (later pages report 0) and paging past the end wraps to page 1. |
| Snap | custom | Public Elasticsearch-shaped API at `careers.snap.com/api/jobs`. **Carries no job description**, so keyword scoring contributes nothing and Snap's scores read low. |
| Rivian | custom | Paginated JSON in front of iCIMS; rejects non-browser User-Agents. |

**A small metro has almost no public job feeds.** Probing 45 regional
employers in one area (health systems, manufacturers, retailers) found
exactly one with a usable feed. Hershey, McCormick, Under Armour, TE
Connectivity and Armstrong run SuccessFactors, which needs a per-company ID
not published on their careers pages. Element Biosciences, by contrast, has
13 of its 16 listings at its own headquarters. **Outside a dozen big metros,
plan on remote work; the local search is not a volume game.** That is why
`matches --remote` exists.

### Known gaps

**Whatnot** (Marina del Rey) — Cloudflare 403s every non-browser client.

**Qualcomm and Dexcom** (both major San Diego employers) run Eightfold, whose API
returns 403 to non-browser clients. Five URL shapes were tried with full browser
headers, `Referer` and `Origin`. This is the biggest remaining hole in the list
and needs checking by hand.

**Five employers moved their hiring out of Southern California** and are recorded in companies.yaml so
they don't get re-added: Bird (now Canada-heavy), Dollar Shave Club (Durham NC), Tala
(Mexico/India/Philippines), OpenX (Krakow/NY), Fandom (Poland/US-remote).

Weights & Biases was acquired by CoreWeave; its roles now post to the CoreWeave board,
which is NJ/NY/Sunnyvale rather than LA.

**Some postings arrive as a link, not a posting.** Hacker News items are two
links and a score, Snap's API carries no description, and Gopuff's are one
line. Drafting against one prints a warning. The pages behind Hacker News
links are Y Combinator's or the company's own, which this tool does not read
(YC's terms forbid scraping), so paste the real posting into the same job
with `jsa fill <job#>` or the form on its job page. Its application and
drafts stay, and discovery will not overwrite the text. See
[ADR 0020](docs/decisions/0020-postings-the-tool-may-not-read.md).


## Test coverage

888 tests, **80% line coverage**, reported as measured rather than tuned.

The distribution is the interesting part. The code that decides what reaches a
document is well covered; the thin parts are network adapters that need live
endpoints to exercise, and argument plumbing over modules that are themselves
tested:

| Module | Coverage | Why |
|---|---|---|
| `render.py` | 99% | Generated documents must be right |
| `tailor.py` | 98% | The fabrication verifier |
| `letter.py` | 98% | Cover letter wording, checked word by word |
| `prep.py` | 98% | Degree and gap drills |
| `scoring.py` | 97% | Every filter decision |
| `salary.py` | 96% | Pay read out of prose |
| `review.py` | 96% | What the dashboard says a draft changed |
| `approvals.py` | 95% | The human-approval gate |
| `doctor.py` | 94% | What will not work yet |
| `web/` | 90% | Dashboard routes |
| `llm.py` | 60% | Error paths need a live provider to reach |
| `cli.py` | 42% | Argument plumbing over tested modules |
| `sources.py` | 27% | Live ATS endpoints; exercised by `jsa verify` |

`jsa verify` and `tools/fabrication_demo.py` cover the network paths against real
endpoints, which unit tests deliberately do not touch.

## What publishing this exposed, and what was done about it

Before this repository was made public, its whole history was audited and
then rewritten twice, on 2026-09-28 and 2026-09-29. What was found and every
decision is in
[ADR 0008](docs/decisions/0008-staying-private-until-publishable.md); the
method, for anyone doing the same to their own fork, is in
[docs/pre-publication-rewrite.md](docs/pre-publication-rewrite.md).

It was audited commit by commit, because publishing publishes every commit: a file deleted in commit 12 is still
readable in commit 11. `tools/scan_history.py` reads every blob reachable from
every ref, every commit message, and every branch name. The checklist for
running it on your own fork is in
[CONTRIBUTING.md](CONTRIBUTING.md#before-you-make-a-fork-public).

**The scanner was proved before it was trusted.** A file containing a fake API
key, a fake address and a fake Windows account path was committed to a scratch
branch. The pre-commit hook refused it, which is the working-tree scanner doing
its job, so it was committed with `--no-verify`. The history scan found all
three. The file was then deleted in a second commit — leaving the working tree
clean, where a HEAD-only scan reports nothing — and the history scan still
found it, naming both the commit that added it and the commit that removed it.
The branch was then deleted, and the finding left the scan's scope, which is
the same boundary `git push` uses. `tests/test_history_scan.py` keeps all of
that as tests, against throwaway repositories rather than this one.

What the audits found, and where it stands:

- **No key, tracker, profile, generated document or coverage file has ever
  been committed.** `.coveragerc` is in history; `.coverage` never was.
- **Personal details in old versions of files** (a home town and postal code
  in the README, contact-detail fragments in two old tests and one commit
  message, and later the author's school, a former employer and the towns of
  the author's own job search). Removed from every commit by the two
  rewrites. `tools/history_allowlist.txt` is empty: nothing is excused.
- **Commits signed with a personal email address.** Every commit now carries
  a GitHub noreply address.
- No workflow uses `pull_request_target`, references an Actions secret, or
  stores a token.

Two gaps worth knowing about. The personal-data rules are read from
*your* `master_profile.yaml`, so they can only find *your* details — a
recruiter's address pasted into a note was invisible until a shape-based US
address rule was added, and that rule is a shape, not understanding. And no
scanner reads prose for intent; the README finding above is what that looks
like.

## Limitations

Worth knowing before you rely on this.

- **Feed coverage is partial and decays.** 50 of 54 configured sources work. Eightfold
  boards (Qualcomm, Dexcom) return 403 to any automated client, and Cloudflare-fronted
  boards return 403 too. Board tokens also change without notice — run `jsa verify`
  periodically, not just once.
- **Scoring is regex, not judgment.** It reads titles, locations and keywords. It will
  miss a good role with an unusual title and keep a bad one with a flattering one. The
  reasons are printed so you can see when it is wrong.
- **Enrichment is a small model reading prose.** Measured at 98% valid output, not
  100%, and its fit verdicts are advisory — the extracted *facts* are the reliable
  part. `NULL` means "unknown", never "no".
- **Nothing is submitted for you.** By design. The tool drafts and tracks; you apply
  and you send. There is no code path that transmits an application or a message.
- **US-only, and it excludes rather than deprioritises.** A job in Berlin scores 0,
  not "low". Internationalising is not a translation job: salary currency, what
  "work authorization" means outside one country, and US-shaped city/state matching
  all have to change together.
- **Pay is read with patterns, not understood.** Measured on 514 postings: 1 of
  253 postings that state pay was missed (a typo in the posting), and a random
  hand-check found no wrong figure after two list-handling fixes. Equity, bonus
  and "competitive" are ignored. A posting with several levels or cities is
  stored as one combined range; its text is kept so you can check it. Where the
  board publishes pay as data, that is used instead: 30 of 30 such figures
  matched the posting page when checked by hand.
- **One user per checkout.** The profile and tracker are single-tenant, and the
  database is SQLite on local disk.
- **Free API tiers log prompts.** Identity never reaches the model, but job
  descriptions and your experience bullets do. Read your provider's terms.
- **Verified on Windows against Python 3.11.9, 3.12.10 and 3.13.15** — the full
  suite and the fresh-clone check pass on all three. Linux and macOS are
  unverified locally; the CI workflow runs the same commands on Ubuntu 26.04
  (pinned), and the badge above shows whether that is currently passing.
- **The Docker image is built and checked in CI, not on the development
  machine** (Docker Desktop cannot run there: WSL returns
  `REGDB_E_CLASSNOTREG`). The `docker` job runs `tools/docker_check.sh` on
  GitHub's Ubuntu 26.04 runner on every push to `main` and `ci/**`, and
  verifies:
  - no personal file reaches any image layer (decoy `.env`, profile, `*.db`,
    documents and logs are planted before the build and searched for);
  - the process runs as uid 1000, not root;
  - `jsa init` and `jsa matches` exit 0 with the example profile and no key;
  - `jsa serve --host 0.0.0.0` refuses without `JSA_ALLOW_PUBLIC_BIND=1`,
    and with it answers on a port published to 127.0.0.1 only, refusing
    foreign `Host` headers;
  - the tracker on the `/data` volume survives a restart and a new container.

  The layer, non-root and bind-guard checks were each run once against a
  deliberately broken build and failed at the expected line (September
  2026). Those runs were linked here until the original GitHub repository
  was deleted when its history was rewritten (ADR 0008); runs belong to a
  repository, so they went with it, and they have not been re-run. To see it
  for yourself, break one on a branch under `ci/` -- delete the `**/.env`
  line from `.dockerignore`, or drop the `USER jsa` line from the `Dockerfile` -- and
  push it. The first real run
  found a leak: `.dockerignore` patterns match from the build root, so `*.db`
  did not exclude a database under `config/`. Fixed with `**/` patterns.

  **Still untested:** `docker compose` itself (the checks use `docker run`
  with the same settings), `discover` and `enrich` inside the container
  (they need the network and an API key), and any host other than Linux.

## Layout

```
jobsearch/
  profile/
    master_profile.example.yaml # committed template — copy and fill in
    master_profile.yaml         # YOUR data. gitignored.
  db/schema.sql                 # tracker: jobs, applications, documents, approvals
  config/companies.yaml         # feeds, with verification status
  .env.example                  # committed; copy to .env for your API key
  jsa/
    config.py                   # paths, .env loading, preferences
    db.py                       # all SQLite access + additive migrations
    sources.py                  # Greenhouse/Lever/Ashby/Workday/Workable/RSS/custom
    scoring.py                  # the free deterministic match filter
    discover.py                 # feed -> score -> tracker
    llm.py                      # OpenAI-compatible client, fallback chain
    enrich.py                   # LLM pass over survivors, strictly validated
    posting.py                  # how much of a posting a model may read
    places.py                   # a posting's location -> a point on a map
    mapview.py                  # that map, as geometry: no markup, no tiles
    basemap.py                  # the ground under it, from data/, same projection
    web/                        # the local review dashboard:
      __init__.py               #   create_app, the request guard, shared helpers
      routes_*.py               #   one module per area (matches, turbo, job, ...)
      templates/*.html          #   the pages, plain Jinja files
    cli.py                      # python -m jsa ...
  data/                         # Census centroids, outline, basemap; public domain
  tools/build_map_data.py       # rebuilds data/ from its public sources
  tests/                        # 888 tests
```

## Configure it for yourself

Nothing about one candidate is hardcoded, and the example profile is an
invented person — a field technician promoted into sales, two projects still
in development, coursework without a conferred degree. It is shaped that way
on purpose: two roles at one employer exercises the case that used to print
one role's bullets under the other's heading, and the honest credential line
shows how to answer the degree question the tool refuses to lie about. Copy it
and replace every line with your own:

```bash
cp profile/master_profile.example.yaml profile/master_profile.yaml
cp .env.example .env
```

`job_search_preferences` drives everything — `target_titles`, `fallback_titles` and
`fallback_weight` (a second tier ranked below the first), `locations`,
`max_years_experience` and `years_filter` (what a posting asking for more years
does: `reject` drops it, `rank` keeps it ranked lower, `off` ignores years),
`exclude_keywords`, `home_location` and `radius_miles` (below), and `regions`
(named groups for the `matches --near` filter; the dashboard uses a radius
instead).
A degree requirement is never a filter: it is shown on the job as "asks for a
degree" and changes neither whether a job appears nor its score.

**How the resume reads.** All optional; leave them out and the resume is
built as before. `experience[].company_descriptor` is printed beside the
dates. `projects[].start` / `end` let a project cover a gap in the resume
report. `certifications[].display_name` replaces a coded name, and
`components` print after the issuer. Every `skills:` category prints (except
`unverified_candidates`), labelled from the key ("ai_tools" becomes "AI
Tools") unless `skill_labels:` names it. `summaries[].role_kinds` and
`keywords` steer which summary a posting gets. `resume.date_style` is
`month_year` (Mar 2022), `numeric` or `iso`; `resume.skills_include_project_stack`
adds your projects' stacks to the technical skills; `coach.disabled` and
`coach.gap_months` tune the resume report. The example profile shows each one
as a comment, and `jsa doctor` lists the ones you aren't using.

**Distance.** Postings are placed on a map from Census centroids that ship
with the tool — 32,109 towns, 33,791 ZIP codes and a 1:20,000,000 state
outline, no geocoding service and no map tiles, so your home ZIP never leaves
your machine. The ground under the live map ships too: US Census Bureau
boundaries, urban areas and primary roads at 1:500,000, Census lake shores
for close zooms, and Natural Earth lakes and city names, in three levels of
detail (8.3 MB in all). Every file is public domain
and can be rebuilt from its sources with `python tools/build_map_data.py`,
which prints the count and SHA-256 of each so what ships can be checked rather
than trusted. A town within `radius_miles`
(default 40) of `home_location` scores as fully as one you listed by name, so
you do not have to write down every suburb you would commute to. For a
profile at home in Columbus, Ohio, the nearby town of Delaware scores 1.0 at
21 miles without appearing in any list.
Both settings are optional — unset, the origin is the first real place in
`locations`. Further out ranks lower rather than being dropped, a city you
named always wins whatever the mileage, and a posting whose location cannot
be placed is scored on everything else and reported by `jsa doctor` rather
than guessed at. Measured 2026-09-26 over 1,224 open postings: 91.2% are
usable by a radius. See
[ADR 0014](docs/decisions/0014-distance-not-place-names.md). The User-Agent sent to job boards resolves
`JSA_CONTACT_EMAIL`, then your profile's email, then no contact — never a value baked
into the source.
