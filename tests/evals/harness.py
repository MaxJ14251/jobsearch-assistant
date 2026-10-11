"""Drafting evals (plan 25): the model-free half of drafting, case by case.

For each fictional posting in cases/, build what `jsa tailor` builds before
any model call (the bullets chosen, the summary, the role kind, the skill
order, the keyword report) plus the resume report on those bullets, and
compare it with the case's written expectations. Weights come from the
committed corpus, so every run is the same.

tests/test_evals.py fails CI on any miss; tools/eval_report.py prints a
scorecard. An expectation changes only with a reason in its `why:` and in
the commit message.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
TODAY = dt.date(2026, 10, 5)      # the resume report's gap rule reads dates


def load(name: str) -> Any:
    return yaml.safe_load((HERE / name).read_text(encoding="utf-8"))


def profile() -> dict[str, Any]:
    return load("profile.yaml")


def corpus() -> list[str]:
    return [str(t).lower() for t in load("corpus.yaml")["corpus"]]


def cases() -> list[dict[str, Any]]:
    out = []
    for path in sorted((HERE / "cases").glob("*.yaml")):
        case = yaml.safe_load(path.read_text(encoding="utf-8"))
        case["name"] = path.stem
        out.append(case)
    return out


@dataclass
class Plan:
    bullets: list[str]
    summary_family: str | None
    role_kind: str
    skills: dict[str, list[str]]
    keywords_matched: list[str]
    keywords_missing: list[str]
    coach: list[str]
    text: str = ""                 # every rendered word, for "never appears" checks


def plan(case: dict[str, Any], prof: dict[str, Any] | None = None,
         weights: dict[str, float] | None = None) -> Plan:
    from jsa import coach, tailor
    prof = prof or profile()
    if weights is None:
        weights = tailor.tag_weights(None, tailor.vocabulary(prof), corpus=corpus())
    description = case.get("description") or ""
    track = case.get("track") or "engineering"
    title = case.get("title") or ""
    tailor.require_decided_preferences(prof)
    summary = tailor.pick_summary(prof, track, description, title)
    chosen = tailor.select_bullets(prof, description, track, weights=weights, title=title)
    matched, missing = tailor.keyword_gap(description, prof)
    skills = tailor.order_skills(prof, description)
    draft = tailor.TailoredDraft(
        summary_id=summary["id"] if summary else "",
        summary=" ".join(str(summary["text"]).split()) if summary else "",
        bullets=[tailor.DraftBullet(b.id, b.text) for b in chosen],
        keywords_matched=matched, keywords_missing=missing, skills=skills)
    found = [f.id for f in coach.review(prof, draft, today=TODAY)]
    text = " ".join([draft.summary, *(b.text for b in chosen),
                     *(t for terms in skills.values() for t in terms)])
    return Plan([b.id for b in chosen], summary.get("family") if summary else None,
                tailor.role_kind(title, track), skills, matched, missing, found, text)


@dataclass
class Miss:
    case: str
    check: str
    expected: Any
    got: Any

    def __str__(self) -> str:
        return f"{self.case}: {self.check}: expected {self.expected!r}, got {self.got!r}"


@dataclass
class Result:
    case: str
    checks: list[tuple[str, bool]] = field(default_factory=list)
    misses: list[Miss] = field(default_factory=list)


def check(case: dict[str, Any], got: Plan) -> Result:
    expect = case.get("expect") or {}
    result = Result(case["name"])

    def record(name: str, ok: bool, expected: Any, actual: Any) -> None:
        result.checks.append((name, ok))
        if not ok:
            result.misses.append(Miss(case["name"], name, expected, actual))

    for bid in expect.get("bullets_include") or []:
        record(f"includes {bid}", bid in got.bullets, "chosen", got.bullets)
    for bid in expect.get("bullets_exclude") or []:
        record(f"excludes {bid}", bid not in got.bullets, "not chosen", got.bullets)
    if "first_bullet" in expect:
        first = got.bullets[0] if got.bullets else None
        record("first bullet", first == expect["first_bullet"], expect["first_bullet"], first)
    if "first_bullet_in" in expect:
        first = got.bullets[0] if got.bullets else None
        record("first bullet", first in expect["first_bullet_in"],
               expect["first_bullet_in"], first)
    if "summary_family" in expect:
        record("summary", got.summary_family == expect["summary_family"],
               expect["summary_family"], got.summary_family)
    if "role_kind" in expect:
        record("role kind", got.role_kind == expect["role_kind"],
               expect["role_kind"], got.role_kind)
    for category, terms in (expect.get("skills_first") or {}).items():
        head = got.skills.get(category, [])[:len(terms)]
        record(f"skills_first {category}", head == list(terms), list(terms), head)
    for term in expect.get("keywords_matched") or []:
        record(f"keyword {term}", term in got.keywords_matched, "matched", got.keywords_matched)
    for term in expect.get("keywords_missing") or []:
        record(f"gap {term}", term in got.keywords_missing, "reported", got.keywords_missing)
    for term in expect.get("never_says") or []:
        record(f"never says {term}", term.lower() not in got.text.lower(), "absent",
               "present")
    for fid in (expect.get("coach") or {}).get("present") or []:
        record(f"coach {fid}", fid in got.coach, "present", got.coach)
    for fid in (expect.get("coach") or {}).get("absent") or []:
        record(f"no coach {fid}", fid not in got.coach, "absent", got.coach)
    return result


def run_all(weights: dict[str, float] | None = None) -> list[Result]:
    prof = profile()
    return [check(case, plan(case, prof, weights)) for case in cases()]


# --- the subject-aware check (plan 37) ---------------------------------------------


def verifier_cases() -> list[dict[str, Any]]:
    """Cases from verifier/*.yaml, each with its file's defaults filled in."""
    out = []
    for path in sorted((HERE / "verifier").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        defaults = data.get("defaults") or {}
        for case in data.get("cases") or []:
            out.append({**defaults, **case, "name": f"{path.stem}: {case['name']}"})
    return out


def check_verifier(case: dict[str, Any], prof: dict[str, Any] | None = None) -> Result:
    """Run one text through answers.check with the posting given (the
    question, if any, goes nowhere: it must never be a word source), or,
    for a case with a `channel`, through outreach.verify_message."""
    import json
    from jsa import answers, outreach, subject
    from jsa.tailor import FabricationError
    prof = prof or profile()
    job = {"title": case.get("title"), "company": case.get("company"), "location": None}
    text = " ".join(str(case["text"]).split())
    if case.get("channel"):
        job = None if case.get("no_job") else dict(
            job, tech_stack=json.dumps(case.get("stack") or []))
        contact = case.get("recipient")
        try:
            outreach.verify_message(text, prof, job=job, contact=contact)
            problems = []
        except FabricationError as exc:
            problems = [str(exc)]
        found = [s["kind"] for s in outreach.checked_sentences(text, prof, job, contact)]
    else:
        problems = answers.check(text, prof, job, min_words=1, max_words=400,
                                 posting_text=str(case.get("posting") or ""))
        ident = prof.get("identity") or {}
        found = [s.kind for s in subject.sort(case["text"], job["company"],
                                              [ident.get("full_name") or ""])]
    expect = case.get("expect") or {}
    result = Result(case["name"])

    def record(name: str, ok: bool, expected: Any, actual: Any) -> None:
        result.checks.append((name, ok))
        if not ok:
            result.misses.append(Miss(case["name"], name, expected, actual))

    verdict = "pass" if not problems else "refuse"
    record("verdict", verdict == expect.get("verdict"), expect.get("verdict"),
           f"{verdict}: {problems}")
    if "kinds" in expect:
        record("kinds", found == list(expect["kinds"]), list(expect["kinds"]), found)
    if "reason_contains" in expect:
        said = " | ".join(problems).lower()
        record("reason", str(expect["reason_contains"]).lower() in said,
               expect["reason_contains"], problems)
    return result


def run_verifier() -> list[Result]:
    prof = profile()
    return [check_verifier(case, prof) for case in verifier_cases()]
