# Contributing

Thanks for looking. This is a personal job-search tool that happens to be
useful to other people, so the bar is: it must keep working for someone who
isn't me.

## Setup

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Scripts/ on Windows, bin/ elsewhere
cp .env.example .env
cp profile/master_profile.example.yaml profile/master_profile.yaml
.venv/Scripts/python -m jsa init
.venv/Scripts/python -m unittest discover -s tests -t .
```

The suite runs with **no API key and no job data**. Anything that needs either
must `skip`, never fail — a fresh clone has neither, and a test that fails for
every new contributor is a broken test.

```bash
git config core.hooksPath .githooks
```
Installs a pre-commit hook that blocks API keys and personal data.

## The four rules this project is built around

Changes that weaken any of these will not be merged.

1. **No fabrication.** Generated text traces to a `master_profile.yaml` bullet
   id or it is rejected. See `jsa/tailor.py::verify_draft`.
2. **No identity to the API.** Name, address, phone and email are merged into
   documents locally. See `jsa/tailor.py::scrub_prompt`.
3. **No autonomous sending.** Every outbound action needs an `approvals` row
   decided by a human. Enforced by a database trigger, not by convention.
4. **Measure, don't assume.** Claims about model behaviour need a number. The
   benchmarks in the README came from `tools/fabrication_demo.py` and the
   enrichment runs, not from intuition.

## Adding a job board

`jsa/sources.py` holds one adapter per platform. An adapter takes a
`companies.yaml` entry and returns normalized dicts.

Board tokens are **not** derivable from a company name. Before opening a PR,
run `python -m jsa verify` and include the real output — a wrong token 404s, or
worse, silently returns a different company's board.

Platforms already handled: Greenhouse, Lever, Ashby, Workday, Workable, RSS,
plus two company-specific APIs. Eightfold returns 403 to automated clients and
is deliberately unsupported.

## Before you open a PR

```bash
.venv/Scripts/python -m unittest discover -s tests -t .
.venv/Scripts/python tools/scan_secrets.py
.venv/Scripts/python tools/fresh_clone_check.py
```

The last one simulates a clone and follows the README literally. If you had to
deviate from the README to get something working, fix the README — that's the
bug.

## Style

Standard library first. `httpx` covers both job feeds and the LLM, so no
provider SDK is needed. New dependencies need a reason in the PR description.
