# Changelog

Every user-visible change gets a line here (see CONTRIBUTING). The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
version number lives in one place, `jsa/__init__.py`.

## [Unreleased]

### Added

- **Canada** as a second country (`country: CA`): Statistics Canada towns,
  three-character postal areas (a full postal code is never kept), province
  scoring, Canadian dollars, kilometres, "legally entitled to work in
  Canada", Canadian contact formats removed from imported resumes, and a
  national map in Canada's own projection
  ([ADR 0037](docs/decisions/0037-canada.md)). No nationwide job source:
  22 Canadian employers' own boards (`companies_ca.yaml`), each confirmed
  and verified. French pay text is not read yet.

### Changed

- **The extension looks like the dashboard**: its colours, frosted panel and
  buttons, in light and dark, on the panel and the options page. The panel is
  simpler: one main action pinned at the bottom, a × instead of Hide buttons,
  what needs you first, and the filled fields folded away. What it does is
  unchanged, and it still never submits.
- **Everything the tool assumes about the US is in one country pack**
  (`jsa/countries/`), and a profile names its country with
  `job_search_preferences.country` (default US). Nothing changes for a US
  profile; another country can now be added as a pack
  ([ADR 0036](docs/decisions/0036-country-packs.md)).
- **The pay floor is `compensation_floor`**, in your country's currency.
  `compensation_floor_usd` still works; setting both is an error.
- The resume import removes every country's contact formats, not only the
  working country's.
- **The radius is `radius`, in your country's unit** (miles in the US; a
  country pack can use km). `radius_miles` still works and is always miles;
  setting both is an error. Distances are still measured in miles inside.
- Pay reading: "$100,000 TO $140,000" (an upper-case TO), "a minimum annual
  salary of $X ... and a maximum of $Z", and a currency code after a figure
  ("$132,000 - $151,000 CAD" is CAD, not your currency, so it is shown but
  not compared). In the owner's tracker this changed 3 of 2,219 readings,
  each a posting that said CAD.

## [0.3.0] - 2026-10-26

Everything below landed after 0.2.0.

### Changed

- **Outreach drafts keep sentences about the recipient apart from sentences
  about you**, and keep your facts' own wording: 2 of 9 measured messages
  passed the unchanged check, against 0 of 18 before
  ([ADR 0010](docs/decisions/0010-outreach-verification.md)).
- **Any OpenAI-compatible model provider**, not only NVIDIA: set
  `JSA_LLM_BASE_URL`, `JSA_LLM_API_KEY` and `JSA_LLM_MODELS` (and
  `JSA_LLM_ENRICH_MODELS`). Unset, every request is exactly as before.
  NVIDIA's "thinking" field is sent only to NVIDIA unless
  `JSA_LLM_THINKING_SWITCH=1`. `jsa env` shows the provider and models.
- **Job boards no longer receive your email.** Discovery's requests name the
  tool and link to the project; a contact address is added only if you set
  `JSA_CONTACT_EMAIL`. Until now your profile's email was sent when that
  setting was empty. `jsa doctor` says which contact is sent.
- **Degree flags read the posting's own text** on cards, the job page and
  `jsa matches`: "degree or equivalent experience" instead of the model's
  "asks for a degree", which was wrong on 219 of 363 postings. The Matches
  filter is now "Asks for a bachelor's or more" / "Open to equivalent
  experience" ([ADR 0032](docs/decisions/0032-what-postings-ask-for-not-who-gets-hired.md)).

### Added

- **Four more employer boards in the shipped list:** Disney (Workday),
  Flexport, Celonis and Reddit (Greenhouse). Each was seen through The Muse or
  We Work Remotely with no board of its own, and measured before it was added
  ([ADR 0009](docs/decisions/0009-what-the-shipped-feed-list-is.md)).

- **Replies have their own tab**, and a "thank you for applying" receipt
  gets **Got it** (one, or all at once): noted, no stage moves. Also
  `jsa inbox ack [reply#]`.
- **The extension's toolbar icon brings the panel back** on an application
  page, after it was hidden or didn't appear.
- **Add, save and draft a resume from the extension's panel** for a job not
  yet in your tracker or not saved: one model call, up to 10 a day; you still
  approve it in Review ([ADR 0031](docs/decisions/0031-autofill-in-your-browser-you-press-submit.md)).
- Outreach's check no longer lets a claim about you through: the posting's
  stack and the recipient's title and company only in sentences about the
  recipient, the full degree check, and a word check even with no job
  ([ADR 0010](docs/decisions/0010-outreach-verification.md)).
- **Drafting prompts that keep sentences about the employer apart** from
  sentences about you, switched on with the sentence-by-sentence check:
  suggestions now pass 26 of 30 when measured (was 1 of 20), and the company
  answer is drafted again
  ([ADR 0035](docs/decisions/0035-who-each-sentence-is-about.md)).
- **A check that knows who each sentence is about** (written answers and
  suggestions): sentences about the employer may use the
  posting's words, everything about you is checked as before
  ([ADR 0035](docs/decisions/0035-who-each-sentence-is-about.md)).
- **"Record it as applied?"** in the browser extension: after you submit,
  the board's "application received" page brings up the question, and one
  click records it as "I submitted this" does
  ([ADR 0031](docs/decisions/0031-autofill-in-your-browser-you-press-submit.md)).
- **Suggested answers** in the browser extension's panel: the open
  questions a fill left empty, three checked options for one when you press
  Suggest (20 a day), inserted only when you pick one, and your submitted
  answers offered again for the same question
  ([ADR 0034](docs/decisions/0034-suggested-answers.md)).
- **About this company and role** in the browser extension's panel: the
  posting's and the company's education asks and the role's nationwide mix,
  reading the company's own board at most weekly when needed
  ([ADR 0031](docs/decisions/0031-autofill-in-your-browser-you-press-submit.md)).
- **Who holds this kind of job, nationwide**: the education mix of workers
  in a posting's occupation (BLS table 5.3, titles mapped with O*NET), on
  job pages and Turbo cards, with an About this data page
  ([ADR 0033](docs/decisions/0033-who-holds-each-kind-of-job.md)).
- **What postings ask for in education**, read from the text with no model:
  on each job page, for each company (a new Companies page and `jsa
  companies`), and as a Matches option, "Open to equivalent experience"
  ([ADR 0032](docs/decisions/0032-what-postings-ask-for-not-who-gets-hired.md)).

- **A browser extension that fills, and you submit** (`extension/`, loaded
  unpacked in Chrome): fills Greenhouse, Lever and Ashby applications from
  your profile, approved documents and answers, outlines what it left for
  you, and never presses Submit. Pair it from the dashboard's new Extension
  page ([ADR 0031](docs/decisions/0031-autofill-in-your-browser-you-press-submit.md)).
- **An apply session:** "Start applying (N ready)" on Pipeline walks your
  ready jobs one form after another; you submit each, and "I submitted this,
  next" records it and opens the next. It never moves on by itself.

- **Recruitee boards** (`kind: recruitee` in your companies list), in
  discovery, `jsa verify`, `jsa add <link>` and `jsa find`'s live check,
  with the board's pay field when it gives one. SmartRecruiters and Breezy
  HR were checked and declined on their own terms
  ([ADR 0020](docs/decisions/0020-postings-the-tool-may-not-read.md)).

- **Federal jobs from USAJOBS**, asked about your own cities like The Muse,
  once you put a free key and the email you requested it with in `.env`
  (`USAJOBS_API_KEY`, `USAJOBS_EMAIL`). Each posting links to USAJOBS, where
  you apply. `jsa doctor` says when only one of the two is set
  ([ADR 0013](docs/decisions/0013-one-nationwide-source.md)).

### Fixed

- **Suggest leaves start date and availability to you**, like salary: "When
  would you be able to start work?" got drafts about the job instead.
- **An applied job moved back by mistake can be moved to applied again.**
  It was refused as a second application; the original record of what was
  sent is kept as it was.
- **The README can't silently drift from the code** on what can be counted:
  source counts, supported boards and link hosts, scoring weights, hard
  rejects, commands and settings it names, and the test count
  (`tests/test_docs_agree.py`). The weights sentence now includes pay's 10%.
  Line coverage re-measured: 89% (was 80% at 888 tests).
- **The model fallback chain had gone stale.** Of six drafting models, only
  the first still answered on NVIDIA (two 410 Gone, one 404, two timeouts).
  The chain is now the three that answered when probed on 2026-10-24
  (nemotron-3-ultra-550b, gpt-oss-20b, nemotron-3.5-lightning); enrichment
  drops super-120b (404).
- A tracker made before a newer source kind is now rebuilt for it (with a
  backup first); the check used to look for one kind's name only.
- `jsa verify --write` changes only the `verified:` values in
  `companies.yaml`, keeping every comment, and a feed that fails one run is
  reported and left verified. It used to rewrite the whole file without its
  comments and switch off any board that failed once.
- **Setup refuses the example's stand-in cities.** "Start from the example"
  filled "Other cities" with "Your City, ST" and the like, and saving kept
  them as real places.
- **Setup no longer tells you to copy the draft over the profile it just
  became**; step 4 says the draft is your profile now. The page also names
  `JSA_LLM_API_KEY` for other providers, and gives the first search's time
  as about 20 minutes (measured 18 over 60 sources; it said 13).
- **Matches says when the three-per-company cap hides cards** ("11 more
  hidden: at most 3 per company"). A first search over a few boards used
  to read "14 unreviewed, showing 3" with no reason.

## [0.2.0] - 2026-10-05

The first packaged release. Everything below landed after 0.1.0.

### Added

- **Install it as a command:** `pipx install git+https://github.com/MaxJ14251/jobsearch-assistant`
  gives `jsa`. Your files live in your own folder (`JSA_HOME`, or a per-user
  default); a clone keeps its layout. `jsa init` starts your profile and
  `.env` from their templates and never overwrites one.
- **Fill in a stub posting in place:** `jsa fill <job#>` keeps the job's
  number, application and drafts, and discovery never overwrites the text
  ([ADR 0020](docs/decisions/0020-postings-the-tool-may-not-read.md)).
- **Read replies from Gmail**, read-only, and suggest stage changes for you
  to confirm ([ADR 0021](docs/decisions/0021-reading-replies-from-the-mailbox.md)).
- **A role search box** on Matches instead of the engineering/sales
  dropdown; it filters the cards, the map and your applications together.
- **Import a resume** (.docx or PDF) into a draft profile, from the command
  line or a dashboard drop zone ([ADR 0022](docs/decisions/0022-importing-a-resume.md)).
- **The resume report** (`jsa coach`, and beside Approve), and reason codes
  on rejection ([ADR 0023](docs/decisions/0023-the-resume-report.md),
  [ADR 0024](docs/decisions/0024-reason-codes-on-rejection.md)).
- **Backups:** `jsa backup`, a snapshot before risky upgrades, and `jsa
  restore` ([ADR 0025](docs/decisions/0025-backing-up-the-tracker.md)).
- **`jsa outcomes`:** plain counts of what happened to each application, by
  source, role kind, cover letter, redraft, speed or channel.
- **Interview prep is suggested** when an application reaches an interview
  stage, with a button on the job page.
- **Turbo:** swipe through matches; right saves and drafts in the
  background, and nothing is ever submitted
  ([ADR 0026](docs/decisions/0026-turbo-decides-interest-never-submits.md)).
- **Application answers:** profile facts verbatim and checked written
  answers, each with a copy button ([ADR 0027](docs/decisions/0027-application-answers.md)).
- **`jsa daily`:** backup, discovery and inbox in one run you schedule, and a
  "since last run" banner ([ADR 0028](docs/decisions/0028-a-daily-run-the-owner-schedules.md)).
- **Ranking nudged by your own swipes:** bounded, explained and resettable
  ([ADR 0029](docs/decisions/0029-ranking-nudged-by-your-swipes.md)).
- **A setup page for newcomers:** import, a preferences form, first-run
  adopt and a first search in the background.
- **`jsa find`:** look for a LinkedIn or Indeed listing on the employer's own
  board, without ever opening LinkedIn or Indeed
  ([ADR 0030](docs/decisions/0030-finding-a-listing-on-the-employers-board.md)).
- **An apply-by-hand checklist** on the job page, and where you applied
  recorded with "I applied" ([ADR 0012](docs/decisions/0012-what-applied-means.md)).
- **`jsa usage`:** every model call counted (retries and fallbacks too),
  tokens and failures, an estimated cost when you set prices; no prompt or
  reply text is kept.
- **Drafting evals in CI:** 16 fictional postings with written expectations
  for which bullets, summary and skills a resume leads with.

### Changed

- **Tailored resumes:** experience renders without ids, dates read
  "Mar 2022", every skill category prints, and ties prefer a released
  project with a public repo ([ADR 0005](docs/decisions/0005-role-kind-and-tag-vocabulary.md)).
- A warning when drafting, prepping or writing outreach against a
  near-empty posting.
- A faster first discovery: Workday skips detail requests for titles that
  can't match.
- Every dashboard form has a size limit, and every POST states its length.
- The dashboard's code is a package (`jsa/web/`) with its pages as template
  files; the pages are unchanged.
- Shipped files (schema, map data, companies list, example profile, `.env`
  template) moved into `jsa/resources/`.

### Fixed

- The fixes from the October review ([docs/reviews/2026-10-review.md](docs/reviews/2026-10-review.md)),
  among them:
  - a late draft can no longer move an applied, interview or closed
    application back to "ready";
  - the dashboard no longer fails while another command upgrades the tracker;
  - "Check email" no longer blocks the dashboard;
  - a half-finished `jsa restore --all` can no longer happen;
  - `jsa find` no longer calls a near-name company or a same-name city "the
    same job";
  - a resume import no longer lets a name slip to the model;
  - interview prep works for someone who holds a degree.

## [0.1.0] - 2026-09-14

- Initial release: discovery from public job boards, scoring, tailored
  resumes and cover letters drafted from your own profile and checked
  against it, human approval for every document, outreach drafts, interview
  prep, and a local dashboard.
