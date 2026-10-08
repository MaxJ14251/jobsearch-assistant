"""Discovery orchestrator: feeds -> scoring -> tracker."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import json

from . import db, salary, sources
from .config import Preferences, load_profile, load_sources
from .scoring import dedup_key, job_track


@dataclass
class SourceReport:
    company: str
    kind: str
    status: str
    fetched: int = 0
    kept: int = 0
    new: int = 0
    rejected: int = 0
    duplicate: int = 0        # already stored from an employer's own board


def verify_sources(entries: list[dict[str, Any]] | None = None) -> list[SourceReport]:
    """Probe every configured feed. Reports what actually works.

    Board tokens in companies.yaml are guesses until this passes.
    """
    entries = entries if entries is not None else load_sources()
    reports = []
    for entry in entries:
        if entry.get("enabled") is False:
            reports.append(
                SourceReport(entry["company"], entry.get("kind", "?"), "disabled")
            )
            continue
        result = sources.fetch(_verify_context(entry))
        reports.append(
            SourceReport(
                company=entry["company"],
                kind=entry.get("kind", "?"),
                status=result.status if result.ok else f"FAIL {result.status}",
                fetched=len(result.jobs),
            )
        )
    return reports


def _slug(name: str) -> str:
    """A company slug from a name, so an aggregator's "SpaceX" lands on the
    same row as the SpaceX board already in companies.yaml."""
    import re

    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "company"


# Sources asked about the operator's own cities, and the .env settings each
# needs: entry field -> environment variable. Keys stay out of companies.yaml.
NATIONWIDE = {
    "themuse": {"api_key": "MUSE_API_KEY"},
    "usajobs": {"api_key": "USAJOBS_API_KEY", "email": "USAJOBS_EMAIL"},
}


def _verify_context(entry: dict[str, Any]) -> dict[str, Any]:
    """Same context, for `jsa verify`, which runs before a tracker exists."""
    if entry.get("kind") not in NATIONWIDE:
        return entry
    try:
        prefs = Preferences.from_profile(load_profile())
    except Exception:  # noqa: BLE001 - verify reports, it does not refuse
        return entry
    return _with_context(entry, prefs)


def _with_context(entry: dict[str, Any], prefs: Preferences) -> dict[str, Any]:
    """What a source needs that is not in companies.yaml.

    Every other feed is one employer's board and needs nothing. An aggregator
    is asked about the operator's own cities, and its key belongs in .env
    rather than in a file that gets committed.
    """
    if entry.get("kind") == "workday":
        # Lets the fetcher skip detail requests for title-rejected postings
        # (Plan 3). Only discovery passes it: verify probes the whole board.
        return {**entry, "title_filter": prefs}
    if entry.get("kind") not in NATIONWIDE:
        return entry
    import os
    settings = {field: os.environ.get(var, "")
                for field, var in NATIONWIDE[entry["kind"]].items()}
    return {**entry, "locations": prefs.locations, **settings}


def discover(
    min_score: float = 0.35,
    only_verified: bool = True,
    db_path: Path | None = None,
) -> list[SourceReport]:
    """Poll feeds, score listings, write keepers to the tracker (`db_path`,
    or the configured one)."""
    profile = load_profile()
    prefs = Preferences.from_profile(profile)
    entries = load_sources()
    # An older tracker may predate a source kind or a column this run needs.
    upgraded = db.upgrade(db_path) if db_path else db.upgrade()

    con = db.connect(db_path) if db_path else db.connect()
    reports: list[SourceReport] = []
    if upgraded:
        reports.append(SourceReport("(tracker)", "schema",
                                    "upgraded: " + ", ".join(upgraded)))
    try:
        for entry in entries:
            company = entry["company"]
            kind = entry.get("kind", "?")

            if entry.get("enabled") is False:
                reports.append(SourceReport(company, kind, "disabled"))
                continue
            if only_verified and not entry.get("verified"):
                reports.append(SourceReport(company, kind, "skipped (unverified)"))
                continue

            company_id = db.upsert_company(
                con,
                name=company,
                slug=entry["slug"],
                careers_url=entry.get("careers_url"),
                priority=int(entry.get("priority", 3)),
            )
            url = sources.feed_url(entry) or ""
            source_id = db.upsert_source(
                con,
                name=f"{entry['slug']}-{kind}",
                kind=kind,
                url=url,
                company_id=company_id,
            )
            # Commit before the network: a feed can take minutes, and an open
            # write transaction meanwhile locks out the dashboard's saves and
            # passes and Turbo's drafter ("database is locked"; plan 18).
            con.commit()

            result = sources.fetch(_with_context(entry, prefs))
            # Workday skips detail requests for title-rejected postings (Plan
            # 3); they were seen, and they are filtered, so say so.
            report = SourceReport(company, kind, result.status,
                                  fetched=len(result.jobs) + result.skipped,
                                  rejected=result.skipped)

            if not result.ok:
                report.status = f"FAIL {result.status}"
                db.mark_source_polled(con, source_id, report.status)
                reports.append(report)
                con.commit()
                continue

            seen: list[str] = list(result.skipped_ids)
            for job in result.jobs:
                # An aggregator names a different employer on every posting.
                job_company_id = company_id
                employer = str(job.pop("employer", "") or "").strip()
                if employer:
                    job_company_id = db.upsert_company(
                        con, name=employer, slug=_slug(employer))
                seen.append(job["external_id"])
                # Pay is read before scoring, because scoring ranks on it.
                # The board's own pay field when it has one (n22), which is
                # the employer's declared range; the description otherwise.
                pay = job.pop("pay", None) or salary.extract(job.get("description"))
                job.update(salary.columns(pay))
                score, reasons = _score(job, prefs)
                if score < min_score:
                    report.rejected += 1
                    continue
                payload = {
                    **job,
                    "company_id": job_company_id,
                    "source_id": source_id,
                    "dedup_key": dedup_key(job_company_id, job["title"], job.get("location")),
                    "track": job_track(job["title"], prefs),
                    "match_score": score,
                    "match_reasons": reasons,
                }
                if db.find_duplicate(con, payload) is not None:
                    report.duplicate += 1
                job_id, is_new = db.upsert_job(con, payload)
                report.kept += 1
                report.new += int(is_new)

            db.close_missing_jobs(con, source_id, seen)
            db.mark_source_polled(con, source_id, "ok")
            reports.append(report)
            con.commit()
    finally:
        con.close()
    return reports


@dataclass
class RescoreReport:
    jobs: int = 0
    with_pay: int = 0
    rejected_by_floor: int = 0
    changed: int = 0


def rescore(con, prefs: Preferences) -> RescoreReport:
    """Re-read pay and re-score every stored listing. No network.

    Discovery scores listings as it fetches them; a scoring change would
    otherwise wait for the next poll, and the before/after could not be
    compared on the same postings. Nothing is deleted: a job that now scores
    below the discovery minimum stays, with its new score and reasons.
    """
    report = RescoreReport()
    rows = con.execute(
        "SELECT id, title, description, location, remote, match_score, "
        "salary_min, salary_max, salary_period, salary_text, salary_currency, "
        "salary_source FROM jobs WHERE archived_at IS NULL").fetchall()
    for row in rows:
        job = dict(row)
        # A figure from the board's own pay data is not re-read from the
        # text: the text never had it (n22), and a re-read would erase it.
        if job.get("salary_source") != "field":
            job.update(salary.columns(salary.extract(job.get("description"))))
        score, reasons = _score(job, prefs)
        report.jobs += 1
        report.with_pay += int(job["salary_min"] is not None)
        report.rejected_by_floor += int(score == 0 and "floor" in reasons[0])
        report.changed += int(score != row["match_score"])
        con.execute(
            "UPDATE jobs SET salary_min = :salary_min, salary_max = :salary_max, "
            "salary_period = :salary_period, salary_text = :salary_text, "
            "salary_currency = :salary_currency, salary_source = :salary_source, "
            "match_score = :score, match_reasons = :reasons WHERE id = :id",
            {**{k: job.get(k) for k in ("salary_min", "salary_max", "salary_period",
                                        "salary_text", "salary_currency",
                                        "salary_source")},
             "score": score, "reasons": json.dumps(reasons), "id": row["id"]})
    # What each posting asks for in education, read again with today's rules
    # (plan 32). No network either.
    db.backfill_degree(con, only_missing=False)
    return report


def _score(job: dict[str, Any], prefs: Preferences) -> tuple[float, list[str]]:
    from .scoring import score_job

    return score_job(job, prefs)
