"""Discovery orchestrator: feeds -> scoring -> tracker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import db, sources
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
        result = sources.fetch(entry)
        reports.append(
            SourceReport(
                company=entry["company"],
                kind=entry.get("kind", "?"),
                status=result.status if result.ok else f"FAIL {result.status}",
                fetched=len(result.jobs),
            )
        )
    return reports


def discover(
    min_score: float = 0.35,
    only_verified: bool = True,
) -> list[SourceReport]:
    """Poll feeds, score listings, write keepers to the tracker."""
    profile = load_profile()
    prefs = Preferences.from_profile(profile)
    entries = load_sources()

    con = db.connect()
    reports: list[SourceReport] = []
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

            result = sources.fetch(entry)
            report = SourceReport(company, kind, result.status, fetched=len(result.jobs))

            if not result.ok:
                report.status = f"FAIL {result.status}"
                db.mark_source_polled(con, source_id, report.status)
                reports.append(report)
                con.commit()
                continue

            seen: list[str] = []
            for job in result.jobs:
                seen.append(job["external_id"])
                score, reasons = _score(job, prefs)
                if score < min_score:
                    report.rejected += 1
                    continue
                job_id, is_new = db.upsert_job(
                    con,
                    {
                        **job,
                        "company_id": company_id,
                        "source_id": source_id,
                        "dedup_key": dedup_key(company_id, job["title"]),
                        "track": job_track(job["title"], prefs),
                        "match_score": score,
                        "match_reasons": reasons,
                    },
                )
                report.kept += 1
                report.new += int(is_new)

            db.close_missing_jobs(con, source_id, seen)
            db.mark_source_polled(con, source_id, "ok")
            reports.append(report)
            con.commit()
    finally:
        con.close()
    return reports


def _score(job: dict[str, Any], prefs: Preferences) -> tuple[float, list[str]]:
    from .scoring import score_job

    return score_job(job, prefs)
