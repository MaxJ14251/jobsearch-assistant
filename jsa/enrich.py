"""LLM enrichment of job listings.

The deterministic filter in `scoring.py` is fast, free and auditable, but it
reads titles and keywords. It structurally cannot see facts that live in prose:

    "Bachelor's degree in Computer Science or equivalent experience"
    "...must be eligible to obtain a US security clearance"

Measured on 50 sampled listings (2026-09-14): **29 required a degree** and 4
required a clearance that the title never mentioned. For a candidate without a
conferred degree that is the single most consequential fact in a posting.

So this module runs a small, cheap model over the listings that *survived* the
free filter — roughly 500 per run rather than 6,900 — and records what it finds.

Two rules learned the hard way, both enforced below:

1. **thinking must be off.** nemotron-3.5-lightning is a reasoning model. Left
   on, it spends the whole token budget thinking and never answers: 2% parse
   rate, 73s median. Off: 98% parse rate, 2.5s median.

2. **Validate the shape, never trust it.** With reasoning on, the model echoed
   the prompt's own schema, and a naive JSON scan returned
   `["up to 5 primary technologies"]` as if it were extracted data — confident,
   well-formed and wrong. Every field is type-checked here; anything that fails
   becomes NULL, which means "unknown", not "no".

Enrichment never rejects a listing. "Bachelor's required" is often boilerplate
a hiring manager ignores, so the decision stays with the human — this only
stops the information from being invisible.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from . import db, llm

# Small and cheap: this runs hundreds of times per pass.
ENRICH_MODELS = [
    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "nvidia/nemotron-3-super-120b-a12b",
    "openai/gpt-oss-20b",
]

MAX_DESCRIPTION_CHARS = 6000
DEFAULT_WORKERS = 4

SYSTEM = (
    "You extract structured facts from job postings. Output raw JSON only — "
    "no prose, no markdown fences, no reasoning."
)

PROMPT = """Extract these facts from the job posting. Output one JSON object:

{{"degree_required": true if a bachelor's degree or higher is required, false if
   explicitly not required or if equivalent experience is accepted, null if unclear,
 "clearance_required": true if a security clearance is required or must be obtainable, else false,
 "years_required": minimum years of experience as an integer, or null if unstated,
 "seniority": one of "intern","entry","junior","mid","senior","staff","principal",
 "tech_stack": array of up to 5 primary technologies actually used in the role,
 "note": one sentence under 15 words explaining the degree/clearance finding}}

TITLE: {title}
POSTING:
{description}"""

# MUST stay a subset of the CHECK constraint on jobs.seniority in db/schema.sql.
# These drifted apart once — the validator accepted "lead", the column did not,
# and the pass died mid-run on IntegrityError. tests/test_enrich.py now parses
# the schema and asserts the two agree.
SENIORITY = {"intern", "entry", "junior", "mid", "senior", "staff", "principal"}

# The model uses its own words; map the common ones onto the schema's.
SENIORITY_ALIASES = {
    "lead": "senior",
    "team lead": "senior",
    "tech lead": "senior",
    "entry-level": "entry",
    "entry level": "entry",
    "junior/entry": "junior",
    "new grad": "entry",
    "graduate": "entry",
    "associate": "junior",
    "mid-level": "mid",
    "mid level": "mid",
    "midlevel": "mid",
    "director": "principal",
    "staff/principal": "staff",
}


@dataclass
class Enrichment:
    """A validated result. Any field may be None, meaning 'unknown'."""

    degree_required: bool | None = None
    clearance_required: bool | None = None
    years_required: int | None = None
    seniority: str | None = None
    tech_stack: list[str] = field(default_factory=list)
    note: str = ""


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    return None


def validate(data: Any) -> Enrichment:
    """Coerce a model response into an Enrichment, discarding anything odd.

    Deliberately permissive about *missing* fields and strict about *wrong*
    ones: a bad value must not become a confident-looking record.
    """
    if not isinstance(data, dict):
        raise ValueError(f"expected an object, got {type(data).__name__}")

    years = data.get("years_required")
    if isinstance(years, bool) or not isinstance(years, (int, float)):
        years = None
    else:
        years = int(years)
        if not 0 <= years <= 40:      # 40+ is a parse artefact, not a requirement
            years = None

    seniority = data.get("seniority")
    if isinstance(seniority, str):
        seniority = seniority.strip().lower()
        seniority = SENIORITY_ALIASES.get(seniority, seniority)
        if seniority not in SENIORITY:
            seniority = None
    else:
        seniority = None

    raw_stack = data.get("tech_stack")
    stack: list[str] = []
    if isinstance(raw_stack, list):
        for item in raw_stack[:5]:
            if isinstance(item, str) and 1 <= len(item.strip()) <= 40:
                text = item.strip()
                # Reject echoes of the prompt's own placeholder text.
                if "primary technolog" in text.lower():
                    continue
                stack.append(text)

    note = data.get("note")
    note = note.strip()[:200] if isinstance(note, str) else ""

    return Enrichment(
        degree_required=_as_bool(data.get("degree_required")),
        clearance_required=_as_bool(data.get("clearance_required")),
        years_required=years,
        seniority=seniority,
        tech_stack=stack,
        note=note,
    )


def enrich_one(title: str, description: str, metrics=None) -> Enrichment:
    """Call the model for a single listing. Raises LLMError or ValueError."""
    prompt = PROMPT.format(
        title=title or "(untitled)",
        description=(description or "")[:MAX_DESCRIPTION_CHARS],
    )
    data, _usage = llm.complete_json(
        prompt,
        system=SYSTEM,
        models=ENRICH_MODELS,
        max_tokens=500,
        temperature=0.1,
        thinking=False,      # non-negotiable; see the module docstring
        attempts=2,
    )
    if metrics is not None:
        metrics.record(_usage)
    return validate(data)


def pending(
    con: sqlite3.Connection, *, min_score: float, limit: int, force: bool
) -> list[sqlite3.Row]:
    """Listings worth spending a call on, best matches first.

    Re-enriches when the posting text changed since last time, so an edited
    repost does not keep stale facts.
    """
    where = [
        "j.archived_at IS NULL",
        "j.closed_at IS NULL",
        "j.description IS NOT NULL",
        "length(j.description) > 300",
        "COALESCE(j.match_score, 0) >= :min_score",
    ]
    if not force:
        where.append(
            "(j.enriched_at IS NULL OR "
            " j.enrichment_hash IS NOT j.description_hash)"
        )
    return con.execute(
        f"""SELECT j.id, j.title, j.description, j.description_hash
              FROM jobs j
             WHERE {' AND '.join(where)}
             ORDER BY j.match_score DESC
             LIMIT :limit""",
        {"min_score": min_score, "limit": limit},
    ).fetchall()


def save(con: sqlite3.Connection, job_id: int, e: Enrichment,
         model: str, desc_hash: str | None) -> None:
    con.execute(
        """UPDATE jobs
              SET degree_required    = :degree,
                  clearance_required = :clearance,
                  years_required     = :years,
                  seniority          = COALESCE(:seniority, seniority),
                  tech_stack         = :stack,
                  enrichment_note    = :note,
                  enriched_at        = :now,
                  enrichment_model   = :model,
                  enrichment_hash    = :hash
            WHERE id = :id""",
        {
            "degree": None if e.degree_required is None else int(e.degree_required),
            "clearance": None if e.clearance_required is None else int(e.clearance_required),
            "years": e.years_required,
            "seniority": e.seniority,
            "stack": json.dumps(e.tech_stack) if e.tech_stack else None,
            "note": e.note or None,
            "now": db.utcnow(),
            "model": model,
            "hash": desc_hash,
            "id": job_id,
        },
    )


@dataclass
class EnrichReport:
    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    degree_required: int = 0
    clearance_required: int = 0
    errors: list[str] = field(default_factory=list)


def run(
    *,
    min_score: float = 0.5,
    limit: int = 200,
    force: bool = False,
    workers: int = DEFAULT_WORKERS,
    progress: bool = True,
    metrics=None,
) -> EnrichReport:
    """Enrich pending listings. Safe to interrupt — each row commits as it lands."""
    con = db.connect()
    report = EnrichReport()
    try:
        rows = pending(con, min_score=min_score, limit=limit, force=force)
        report.attempted = len(rows)
        if not rows:
            return report

        def work(row: sqlite3.Row):
            try:
                return row, enrich_one(row["title"], row["description"], metrics), None
            except Exception as exc:  # noqa: BLE001 — one bad row must not stop the pass
                return row, None, f"{type(exc).__name__}: {str(exc)[:90]}"

        done = 0
        with cf.ThreadPoolExecutor(max_workers=workers) as pool:
            for row, result, error in pool.map(work, rows):
                done += 1
                if error or result is None:
                    report.failed += 1
                    if len(report.errors) < 5:
                        report.errors.append(f"{row['title'][:40]}: {error}")
                else:
                    save(con, row["id"], result, ENRICH_MODELS[0],
                         row["description_hash"])
                    con.commit()
                    report.succeeded += 1
                    report.degree_required += int(result.degree_required is True)
                    report.clearance_required += int(result.clearance_required is True)
                if progress and done % 25 == 0:
                    print(f"    ...{done}/{len(rows)}", flush=True)
    finally:
        con.close()
    return report
