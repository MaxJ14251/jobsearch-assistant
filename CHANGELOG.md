# Changelog

Every user-visible change gets a line here (see CONTRIBUTING). The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
version number lives in one place, `jsa/__init__.py`.

## [Unreleased]

### Added

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

- A tracker made before a newer source kind is now rebuilt for it (with a
  backup first); the check used to look for one kind's name only.
- `jsa verify --write` changes only the `verified:` values in
  `companies.yaml`, keeping every comment, and a feed that fails one run is
  reported and left verified. It used to rewrite the whole file without its
  comments and switch off any board that failed once.

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
