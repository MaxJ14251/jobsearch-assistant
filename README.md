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

Python 3.12 is installed at `%LOCALAPPDATA%\Programs\Python\Python312`. From this
directory:

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt
```

```bash
.venv/Scripts/python -m jsa init
```

Set your API key (needed from step 4 onward, not for discovery):

```bash
setx ANTHROPIC_API_KEY "sk-ant-..."
```

## Usage

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
Polls verified feeds, scores every listing, writes keepers to the tracker. Listings
that vanish from a feed are marked closed rather than deleted.

```bash
.venv/Scripts/python -m jsa matches --local --limit 20
```
Shows unreviewed matches, best first, each with the reasons behind its score.
`--local` restricts to commuting range of Example Town; `--per-company` (default 3)
stops one large board from filling the page — SpaceX alone posts 2,373 reqs.

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
queues it for your approval. The job must be saved first. Every sentence traces
to a bullet id or the draft is refused — a posting demanding Kubernetes gets a
gap report, not a Kubernetes bullet.

Two limits worth knowing. The model sees the first 4,000 characters of a
posting, and **88% of postings in this tracker are longer than that** — so
requirements stated late cannot influence the draft. The command says so when it
truncates. And a redraft needs `--force`, producing a new version, a new file
and a new pending approval; the old one is never overwritten, because approval
is per version.

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
rejection is required: a redraft has nothing to work from without it. A decision
is per document version and is never inherited by a redraft.

```bash
.venv/Scripts/python -m jsa applied 421
```
Records that **you** submitted it — the tool cannot observe that and never will.
It does not require an approved document first. The approval gate exists to stop
the agent acting on its own, not to stop you applying with a resume you wrote by
hand; it notes the absence rather than blocking you.

```bash
.venv/Scripts/python -m jsa serve
```
Local review dashboard on 127.0.0.1 — matches with their flags, the pipeline, and
the approval queue. Approve/Reject go through the same code path as the CLI, so the
human-approval guarantee is identical.

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

One boundary bug worth noting, found by these tests: `C\+\+` never matches,
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

Discovery covers three markets, because two relocations are live options:
Los Angeles (current), San Diego, and south-central PA around Example Town [postal code]
(family home). `matches --near {la,sd,pa}` filters to commuting range;
`matches --remote` covers the roles that work from any of them.

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

Added 2026-09-14, since both are live relocation options.

*San Diego (10):* Shield AI, Tealium, Kyriba, Mitek, Illumina, Petco, GoFundMe,
Airspace, ClickUp, Element Biosciences. Element Biosciences has the highest SD
density found — 13 of 16 listings at the San Diego HQ.

*PA / Baltimore (5):* T. Rowe Price (Baltimore, ~1hr from Example Town), Johnson Controls
(Example Town facility), Highmark, Geisinger, Becton Dickinson. Gopuff and Duolingo are
configured but flagged non-commutable — Philadelphia is ~2hr from Example Town and
Pittsburgh ~3.5hr — so they count only for remote-eligible roles.

**Your home town has essentially no local tech market.** Across two rounds I
probed 45 regional employers — Utz (the largest employer in Example Town itself), WellSpan,
Dentsply Sirona, Harley-Davidson, Rite Aid, D&H, Penn State Health, Johns Hopkins,
Example Town General and others. Exactly one, T. Rowe Price, exposes a usable feed, and
it's in Baltimore. Example Town, McCormick, Under Armour, TE Connectivity and Armstrong run
SuccessFactors, which needs a per-company ID not published on their careers pages.
**For Example Town, plan on remote work; the local search is not a volume game.**

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

213 tests, **59% line coverage**, reported as measured rather than tuned.

The distribution is the interesting part. The code that decides what reaches a
document is well covered; the thin parts are network adapters that need live
endpoints to exercise:

| Module | Coverage | Why |
|---|---|---|
| `render.py` | 97% | Generated documents must be right |
| `approvals.py` | 97% | The human-approval gate |
| `scoring.py` | 92% | Every filter decision |
| `tailor.py` | 83% | The fabrication verifier |
| `web.py` | 71% | Dashboard routes |
| `prep.py` | 69% | Degree and gap drills |
| `sources.py` | 27% | Live ATS endpoints; exercised by `jsa verify` |
| `cli.py` | 13% | Argument plumbing over tested modules |

`jsa verify` and `tools/fabrication_demo.py` cover the network paths against real
endpoints, which unit tests deliberately do not touch.

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
- **Compensation does not affect ranking yet.** Nothing extracts salary from a
  posting, so every `salary_min` is null. A pay floor you set is stored and
  validated but not yet applied — deliberately, rather than shipping a filter that
  silently does nothing.
- **One user per checkout.** The profile and tracker are single-tenant, and the
  database is SQLite on local disk.
- **Free API tiers log prompts.** Identity never reaches the model, but job
  descriptions and your experience bullets do. Read your provider's terms.
- **Tested on Windows with Python 3.12.** CI covers 3.11–3.13 on Linux. macOS is
  unverified.

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
