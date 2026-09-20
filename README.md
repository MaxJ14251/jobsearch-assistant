# Agentic Job Search Assistant

[![tests](https://github.com/MaxJ14251/jobsearch-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/MaxJ14251/jobsearch-assistant/actions/workflows/ci.yml)

Pulls job listings from 50 company career systems, filters ~6,900 postings down to a
reviewable shortlist, extracts the disqualifying facts buried in the prose, and hands
you the final call on every application.

**United States only.** Non-US locations are excluded outright, not ranked low — so
if you are job hunting outside the US, this will return nothing and the emptiness
will not explain itself. That scope is deliberate; see
[docs/decisions/0002-geographic-scope.md](docs/decisions/0002-geographic-scope.md).

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
[0.90] T. Rowe Price — Full Stack Software Engineer, AI Lab
       New York, NY; Baltimore, MD (onsite)
       ⚑ DEGREE REQUIRED  |  2+ yrs
       stack: Java, Python, JavaScript, AWS, Azure
       note: BS or MS required or equivalent experience accepted
       · title matches 'AI Engineer' (100%)
       · located in Baltimore, MD
```

## Status

| # | Module | State |
|---|--------|-------|
| 1 | Master profile | **Done** — [master_profile.example.yaml](profile/master_profile.example.yaml) |
| 2 | Tracker DB schema | **Done** — [db/schema.sql](db/schema.sql), validated on SQLite |
| 3 | Job discovery | **Done** — 45 live feeds across LA, San Diego and PA/MD |
| 3b | LLM enrichment | **Done** — degree/clearance/stack facts the filter can't see |
| 4 | Resume/cover tailoring | **Done** — verifier, scrubber, ATS-safe .docx, provenance |
| 5 | Review dashboard | **Done** — `jsa serve`, loopback only |
| 6 | Interview prep + outreach | **Done** — degree/gap drills, draft-only outreach |

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

**3. Ask what is still missing:**

```bash
.venv/Scripts/python -m jsa doctor
```

It names every field that will stop the tool working, says what to write
instead, and changes nothing. Exits 0 when you are ready. Run it again after
edits.

**4. Find jobs** (no API key needed; takes a few minutes):

```bash
.venv/Scripts/python -m jsa discover
```

**5. Read what it found:**

```bash
.venv/Scripts/python -m jsa matches --limit 20
```

Each match shows its score with the reasons behind it — title, location,
keywords, seniority, pay — so you can see when the tool is wrong.

**6. Add your API key.** Steps 7 and 8 call a language model; the rest never
does. Copy the example and put your key in it:

```bash
cp .env.example .env
```

`.env` holds `NVIDIA_API_KEY=...`. Get one free at build.nvidia.com. The key
never leaves that file, and your name, address, phone and email are never sent
to the model — a check refuses the request if they appear in a prompt.

**7. Save a job and draft for it** (one model call each):

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
the boards listed in `config/companies.yaml`, and you add your own.

**Where the shipped feeds point.** `config/companies.yaml` starts with 45 tech
employers, most of them hiring in the Bay Area, New York and Seattle. Measured
from a profile based in Columbus, Ohio: the matches that came back were almost
entirely remote roles, because those companies post few jobs in Ohio. If you
are not in a coastal tech hub, add local employers' boards to that file —
`jsa verify` tells you which tokens actually work — or lean on remote.

## Usage

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

**How pay counts.** About half of postings state pay; `jsa/salary.py` reads it
from the text (ADR 0006). Higher pay ranks higher for everyone, on the *bottom*
of the stated range, and moves a score by at most ±0.05 — enough to order
similar roles, never enough to lift a poor fit over a good one. A posting that
states no pay scores exactly as if it paid the middle of the band, so saying
nothing is not a penalty. If you set `compensation_floor_usd`, a job whose
*top* figure is below it is rejected, with the reason shown; unknown pay is
never rejected.

```bash
.venv/Scripts/python -m jsa rescore
```
Re-reads pay and re-scores every stored listing without polling any feed. Run it
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

Two limits worth knowing. The model sees the first 4,000 characters of a
posting, and **88% of postings in this tracker are longer than that** — so
requirements stated late cannot influence the draft. The command says so when it
truncates. And a redraft needs `--force`, producing a new version, a new file
and a new pending approval; the old one is never overwritten, because approval
is per version.

```bash
.venv/Scripts/python -m jsa contact-add --name "..." --company Replit --title "..."
.venv/Scripts/python -m jsa outreach-draft --contact 1 --job 421 --channel linkedin_connect
.venv/Scripts/python -m jsa outreach-show 1
.venv/Scripts/python -m jsa outreach-mark-sent 1
```
Drafts networking messages. **Nothing here can send.** That is proven, not
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
prompts it, plus two drills that lead every set: the employment gap, and the
degree. **57% of matched postings state a degree requirement and the degree was
not conferred**, so that answer is generated from the profile's exact wording
and checked against a deny-list, verb-family patterns, and a negation window —
"I did not finish the degree" passes; "after I graduated" does not. Answer notes
are first person, so they read as words you can say rather than a briefing about
you. Running again appends; prior prep is never overwritten.

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
.venv/Scripts/python -m jsa applied 421
```
Records that **you** submitted it — the tool cannot observe that and never will.
It does not require an approved document first. The approval gate exists to stop
the agent acting on its own, not to stop you applying with a resume you wrote by
hand; it notes the absence rather than blocking you.

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
.venv/Scripts/python -m jsa serve
```
Local review dashboard on http://127.0.0.1:8765. It covers the whole path from a
match to a resume you can upload yourself:

- **Matches**, and a **Pipeline** grouped by stage, with each application's
  next action, overdue flags, and the same stage moves as the CLI.
- **Job page**: every drafted document, newest version first, with its status
  (pending, approved, rejected and your note), model, the profile bullets it
  used, the keyword gaps it could not honestly fill, and a **Download .docx**
  link. Its interview preps are listed below. A **Save** button starts an
  application; a **Tailor** button runs exactly what `jsa tailor --force`
  runs and queues the draft for review.
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

**Regional duplicates collapse.** Boards post one req per location — "Forward Deployed
Engineer (Korea)", "(West Coast)", "(UK/Europe)". Those share a `dedup_key`, and
`v_new_matches` shows the best-scoring one with a count of the rest.

**Feeds are public JSON only** — the same endpoints each company's own careers page
already calls: Greenhouse `boards-api`, Lever `api.lever.co`, Ashby `posting-api`,
Workday `wday/cxs`, Workable `widget/accounts`, two company-specific APIs, plus RSS.
Descriptive User-Agent, delay between requests, no auth, no scraping behind a login.
Playwright is deliberately absent from requirements.txt.

**Nothing is hard-deleted.** Rows carry `archived_at`.

## Feed coverage

The shipped `config/companies.yaml` covers three US markets: Los Angeles,
San Diego, and south-central Pennsylvania / north Maryland. They are there
because they are the markets this feed list was built and verified against,
not because you have to search in them — `regions:` in your profile names
your own commute areas, `matches --near <region>` filters to them, and
`matches --remote` covers the roles that work from anywhere. Adding a market
means adding its employers here and running `python -m jsa verify`.

*LA / SoCal (16):* ZipRecruiter, Snap, GoodRx, System1, SpaceX, Rocket Lab, Vast, Riot
Games, Scopely, ServiceTitan, Rivian, GoGuardian, Sidecar Health, Boulevard, Tebra,
Match Group.

*AI labs & dev tools (14):* Anthropic, OpenAI, Scale AI, Perplexity, Sierra, Cohere,
LangChain, Replit, Baseten, Modal, Vercel, Notion, Hugging Face, CoreWeave.

*Aggregators (3):* We Work Remotely, Hacker News jobs, Python.org.

Four of these needed work beyond a board token:

| Company | ATS | What it took |
|---|---|---|
| GoodRx | Workday | Site is `careers`, not `GoodRx`. Needs a detail request per job — the listing response says only "2 Locations". |
| ServiceTitan | Workday | `total` comes back only on page 1 (later pages report 0) and paging past the end wraps to page 1. |
| Snap | custom | Public Elasticsearch-shaped API at `careers.snap.com/api/jobs`. **Carries no job description**, so keyword scoring contributes nothing and Snap's scores read low. |
| Rivian | custom | Paginated JSON in front of iCIMS; rejects non-browser User-Agents. |

### San Diego and south-central PA

Added 2026-09-14.

*San Diego (10):* Shield AI, Tealium, Kyriba, Mitek, Illumina, Petco, GoFundMe,
Airspace, ClickUp, Element Biosciences. Element Biosciences has the highest SD
density found — 13 of 16 listings at the San Diego HQ.

*PA / Baltimore (5):* T. Rowe Price (Baltimore), Johnson Controls (Example Town facility),
Highmark, Geisinger, Becton Dickinson. Gopuff and Duolingo are configured but
flagged non-commutable from south-central PA — Philadelphia is about 2hr away
and Pittsburgh about 3.5hr — so they count only for remote-eligible roles.

**South-central PA has essentially no local tech market.** Across two rounds the
feed list probed 45 regional employers — Utz, WellSpan, Dentsply Sirona, Harley-Davidson, Rite Aid, D&H, Penn State Health, Johns Hopkins,
Example Town General and others. Exactly one, T. Rowe Price, exposes a usable feed, and
it's in Baltimore. Example Town, McCormick, Under Armour, TE Connectivity and Armstrong run
SuccessFactors, which needs a per-company ID not published on their careers pages.
**In a market like this one, plan on remote work; the local search is not a
volume game.** The same is true of most of the country outside a dozen metros,
which is why `matches --remote` exists.

### Known gaps

**Whatnot** (Marina del Rey) — Cloudflare 403s every non-browser client.

**Qualcomm and Dexcom** (both major San Diego employers) run Eightfold, whose API
returns 403 to non-browser clients. Five URL shapes were tried with full browser
headers, `Referer` and `Origin`. This is the biggest remaining hole in San Diego
coverage and needs checking by hand.

**Five former LA companies have left the market** and are recorded in companies.yaml so
they don't get re-added: Bird (now Canada-heavy), Dollar Shave Club (Durham NC), Tala
(Mexico/India/Philippines), OpenX (Krakow/NY), Fandom (Poland/US-remote).

Weights & Biases was acquired by CoreWeave; its roles now post to the CoreWeave board,
which is NJ/NY/Sunnyvale rather than LA.


## Test coverage

590 tests, **76% line coverage**, reported as measured rather than tuned.

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
| `web.py` | 90% | Dashboard routes |
| `llm.py` | 60% | Error paths need a live provider to reach |
| `cli.py` | 42% | Argument plumbing over tested modules |
| `sources.py` | 27% | Live ATS endpoints; exercised by `jsa verify` |

`jsa verify` and `tools/fabrication_demo.py` cover the network paths against real
endpoints, which unit tests deliberately do not touch.

## What publishing this would expose

**This repository is private, and stays private until somebody who is not its
author can use it with their own details** — see
[ADR 0008](docs/decisions/0008-staying-private-until-publishable.md), which
also lists what is still missing for that. The audit below is why that is a
decision rather than an oversight.

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

What the audit found across the 27 commits that existed when it ran,
166 blob versions of 77 files:

- **No key, tracker, profile, generated document or coverage file has ever
  been committed.** `.coveragerc` is in history; `.coverage` never was.
- **Sixteen versions of this README named a town and a postal code**, and
  described the feed coverage in the first person. The current file does not.
  The earlier commits still do, and only rewriting all 28 commits would change
  that. It is recorded in `tools/history_allowlist.txt` rather than hidden.

Neither of the last two is being rewritten now. A rewrite changes every commit
SHA, and its real cost is other people's clones — of which there are none while
this is private. That makes deferring the decision to the moment before
publishing the cheap option and the reversible one.
- **Every commit is signed with a personal email address**, as every git commit
  everywhere is. The scan prints it as a NOTE on every run.
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
  stored as one combined range; its text is kept so you can check it.
- **One user per checkout.** The profile and tracker are single-tenant, and the
  database is SQLite on local disk.
- **Free API tiers log prompts.** Identity never reaches the model, but job
  descriptions and your experience bullets do. Read your provider's terms.
- **Verified on Windows against Python 3.11.9, 3.12.10 and 3.13.15** — the full
  suite and the fresh-clone check pass on all three. Linux and macOS are
  unverified locally; the CI workflow runs the same commands on ubuntu-latest,
  and the badge above shows whether that is currently passing.
- **The Docker image is built and checked in CI, not on the development
  machine** (Docker Desktop cannot run there: WSL returns
  `REGDB_E_CLASSNOTREG`). The `docker` job runs `tools/docker_check.sh` on
  GitHub's ubuntu-latest runner on every push to `main` and `ci/**`, and
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
  deliberately broken build and failed at the expected line:
  [layers](https://github.com/MaxJ14251/jobsearch-assistant/actions/runs/35196244847),
  [non-root](https://github.com/MaxJ14251/jobsearch-assistant/actions/runs/35196236847),
  [bind guard](https://github.com/MaxJ14251/jobsearch-assistant/actions/runs/35196240854). The first real run
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
    cli.py                      # python -m jsa ...
  tests/                        # 91 tests
```

## Configure it for yourself

Nothing about one candidate is hardcoded. Copy the example profile and edit:

```bash
cp profile/master_profile.example.yaml profile/master_profile.yaml
cp .env.example .env
```

`job_search_preferences` drives everything — `target_titles`, `fallback_titles` and
`fallback_weight` (a second tier ranked below the first), `locations`,
`max_years_experience` (the hard reject ceiling), `exclude_keywords`, and `regions`
(named commute areas for `matches --near`). The User-Agent sent to job boards resolves
`JSA_CONTACT_EMAIL`, then your profile's email, then no contact — never a value baked
into the source.
