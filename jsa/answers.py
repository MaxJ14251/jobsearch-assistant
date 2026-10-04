"""Copy-ready answers to the questions application forms ask (plan 17).

ADR 0027. Two kinds:

- **Fact answers** come straight from the profile, verbatim, with no model:
  work authorization, sponsorship, relocation, arrangement, links, the
  education line, years of experience. An undecided value says so and is
  never guessed. They are read live, never stored, so they never go stale.
- **Written answers** (why this role, a relevant project, why this company)
  come from one model call over the person's own bullets, and each is held to
  the cover-letter checks (letter.py). One that fails falls back to the
  person's own sentences, with a note (ADR 0007).

There is **no salary answer**: the floor is a threshold, not an asking
figure, and it never leaves the profile (ADR 0001). Nothing here submits
anything or needs approval; answers are copied by hand, like prep.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from . import db, llm

MIN_WORDS, MAX_WORDS = 40, 150
QUESTIONS = {
    "why_role": "Why are you interested in this role?",
    "relevant_project": "Tell us about a relevant project or accomplishment.",
    "why_company": "Why do you want to work at {company}?",
}
# why_company is built from the person's own sentences only, never drafted.
# ADR 0010 measured sentences ABOUT an employer failing a bag-of-words check
# (1 of 9); measured here on 5 of the owner's saved jobs (2026-10-04), with
# one retry naming the refused words: 0 of 5 passed. The plan's rule was
# composed-only under half. See ADR 0027.
COMPOSED_ONLY: frozenset[str] = frozenset({"why_company"})


@dataclass
class Answer:
    key: str
    question: str
    body: str
    source: str          # profile | model | composed | undecided
    note: str = ""


# --- fact answers ------------------------------------------------------------------


def _yes_no(value: Any, field: str) -> tuple[str, str]:
    from .config import ConfigError, parse_tristate
    try:
        decided = parse_tristate(value, field)
    except ConfigError:
        decided = None
    if decided is None:
        return f"Not decided in your profile: set job_search_preferences.{field}", "undecided"
    return ("Yes" if decided else "No"), "profile"


def fact_answers(profile: dict[str, Any], job: dict[str, Any]) -> list[Answer]:
    from .facts import education_line, years_of_experience

    prefs = profile.get("job_search_preferences") or {}
    out = []
    auth = " ".join(str(prefs.get("work_authorization") or "").split())
    out.append(Answer("work_authorization", "Are you authorized to work in the US?",
                      auth or "Not decided in your profile: set "
                              "job_search_preferences.work_authorization",
                      "profile" if auth else "undecided"))
    body, source = _yes_no(prefs.get("needs_visa_sponsorship"), "needs_visa_sponsorship")
    out.append(Answer("sponsorship", "Will you now or in the future require sponsorship?",
                      body, source))
    body, source = _yes_no(prefs.get("willing_to_relocate"), "willing_to_relocate")
    out.append(Answer("relocation", "Are you willing to relocate?", body, source))
    arrangement = [str(a) for a in prefs.get("work_arrangement") or []]
    out.append(Answer("arrangement", "Which work arrangements are you open to?",
                      ", ".join(arrangement) if arrangement else
                      "Not decided in your profile: set job_search_preferences.work_arrangement",
                      "profile" if arrangement else "undecided"))
    for key, label in (("linkedin", "LinkedIn"), ("github", "GitHub"),
                       ("portfolio", "Portfolio"), ("website", "Website")):
        url = (profile.get("links") or {}).get(key)
        if url:
            out.append(Answer(key, f"{label} URL", str(url), "profile"))
    line = education_line(profile)
    out.append(Answer("education", "Education", line or
                      "Not in your profile: add an education entry", "profile" if line else "undecided"))
    years = years_of_experience(profile)
    if years:
        out.append(Answer("years", "Years of experience", years[1], "profile"))
    out.append(Answer("salary", "Salary expectations", _salary_note(job), "profile",
                      "Never filled in for you: your floor is a threshold, not an asking figure."))
    return out


def _salary_note(job: dict[str, Any]) -> str:
    text = (job.get("salary_text") or "").strip()
    if not text and job.get("salary_min"):
        low, high = job.get("salary_min"), job.get("salary_max")
        text = f"${low:,.0f}" + (f"–${high:,.0f}" if high and high != low else "")
        if job.get("salary_period"):
            text += f" per {job['salary_period']}"
    return f"Your call. The posting states {text}." if text else \
        "Your call. The posting states no pay."


# --- written answers ------------------------------------------------------------------


SYSTEM = (
    "You draft short answers to job application questions, in the first person, "
    "using ONLY the candidate's notes. Add no skill, outcome, number, employer "
    "fact or quality the notes don't state. Work the notes describe as ongoing "
    "stays ongoing. Never mention a degree. Return only JSON."
)

PROMPT = """Role: {title} at {company}.

The candidate's summary:
{summary}

The candidate's notes (their own resume bullets):
{bullets}

Write two answers, each {min_words}-{max_words} words:
- "why_role": why this role, from what the notes show they do.
- "relevant_project": one project or piece of work from the notes, what it
  does and what part they did.

Return JSON: {{"why_role": "...", "relevant_project": "..."}}"""


def evidence(con: sqlite3.Connection, job: dict[str, Any],
             profile: dict[str, Any]) -> list[Any]:
    """The bullets an answer may draw on: the latest APPROVED resume's for
    this job, else the ones selection would pick (no model)."""
    import json

    from .tailor import collect_bullets, select_bullets

    sources = collect_bullets(profile)
    row = con.execute(
        "SELECT d.bullet_ids FROM documents d JOIN approvals a "
        "ON a.subject_type = 'document' AND a.subject_id = d.id "
        "WHERE d.job_id = ? AND d.kind = 'resume' AND a.decision = 'approved' "
        "ORDER BY d.version DESC LIMIT 1", (job["id"],)).fetchone()
    if row:
        try:
            chosen = [sources[i] for i in json.loads(row["bullet_ids"] or "[]")
                      if i in sources]
        except ValueError:
            chosen = []
        if chosen:
            return chosen
    return select_bullets(profile, job.get("description") or "",
                          job.get("track") or "engineering", title=job.get("title") or "")


def check(text: str, profile: dict[str, Any], job: dict[str, Any]) -> list[str]:
    """Everything wrong with one answer. Empty means it may be used."""
    from .letter import ongoing_verbs, unsupported_words
    from .prep import DegreeClaimError, assert_no_degree_claim
    from .tailor import do_not_claim, term_pattern

    problems = []
    n = len(text.split())
    if not MIN_WORDS <= n <= MAX_WORDS:
        problems.append(f"{n} words, outside {MIN_WORDS}-{MAX_WORDS}")
    try:
        assert_no_degree_claim(text)
    except DegreeClaimError as exc:
        problems.append(f"implies a degree ({exc})")
    said = {w.lower().strip(".,;:") for w in text.split()}
    for verb in sorted(ongoing_verbs(profile) & said):
        problems.append(f"describes ongoing work as finished ({verb!r})")
    for term in do_not_claim(profile):
        if term and term_pattern(term).search(text.lower()):
            problems.append(f"claims {term!r}, which is on your do-not-claim list")
    new = unsupported_words(text, profile, job)
    if new:
        problems.append("says words your profile does not: " + ", ".join(new[:8]))
    return problems


def _summary(profile: dict[str, Any], job: dict[str, Any]) -> str:
    from .tailor import pick_summary
    s = pick_summary(profile, job.get("track") or "engineering",
                     job.get("description") or "", job.get("title") or "")
    return " ".join(str(s["text"]).split()) if s else ""


def compose(key: str, profile: dict[str, Any], job: dict[str, Any],
            bullets: list[Any]) -> str:
    """The person's own sentences: the summary and their top bullet."""
    summary = _summary(profile, job)
    projects = [b for b in bullets if b.origin == "project"]
    top = (projects or bullets or [None])[0]
    lead = {"why_role": f"I'm applying for the {job.get('title')} role.",
            "relevant_project": "",
            "why_company": f"I'd like to work at {job.get('company')}."}[key]
    parts = [lead] if lead else []
    if key == "relevant_project" and top is not None:
        parts.append(top.text)
        parts.append(summary)
    else:
        parts.append(summary)
        if top is not None:
            parts.append(top.text)
    return " ".join(p for p in parts if p).strip()


def write(con: sqlite3.Connection, job_id: int, profile: dict[str, Any],
          *, models: list[str] | None = None) -> list[Answer]:
    """Draft, check and store the written answers (archiving any older set)."""
    from .tailor import scrub_prompt

    job = con.execute("SELECT j.*, c.name AS company FROM jobs j LEFT JOIN companies c "
                      "ON c.id = j.company_id WHERE j.id = ?", (job_id,)).fetchone()
    if job is None:
        raise ValueError(f"there is no job {job_id}")
    job = dict(job)
    app = con.execute("SELECT id FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    if app is None:
        raise ValueError(f"save job {job_id} first: `jsa save {job_id}`")
    bullets = evidence(con, job, profile)
    prompt = PROMPT.format(
        title=job.get("title") or "the role", company=job.get("company") or "the company",
        summary=_summary(profile, job) or "(none)",
        bullets="\n".join(f"- {b.text}" for b in bullets),
        min_words=MIN_WORDS, max_words=MAX_WORDS)
    scrub_prompt(prompt, profile)          # fails closed before any network call

    model, failure = "", ""
    wanted = [k for k in QUESTIONS if k not in COMPOSED_ONLY]

    def problems_of(drafted: dict[str, Any]) -> dict[str, list[str]]:
        found = {}
        for key in wanted:
            text = " ".join(str(drafted.get(key) or "").split())
            found[key] = check(text, profile, job) if text else ["no answer came back"]
        return found

    drafted: dict[str, Any] = {}
    found: dict[str, list[str]] = {}
    try:
        data, usage = llm.complete_json(prompt, system=SYSTEM, models=models,
                                        max_tokens=900, temperature=0.3,
                                        thinking=False, attempts=2)
        drafted = data if isinstance(data, dict) else {}
        model = usage.model or ""
        found = problems_of(drafted)
        refused = {k: p for k, p in found.items() if p}
        if refused:
            # One retry that says what failed, as the cover letter does
            # (letter.write): re-rolling blind repeats the same words.
            from .letter import unsupported_words
            banned = sorted({w for k in refused
                             for w in unsupported_words(str(drafted.get(k) or ""),
                                                        profile, job)})
            retry = (prompt + "\n\nYour previous answers were refused: "
                     + "; ".join(f"{k}: {', '.join(p[:2])}" for k, p in refused.items())
                     + ".\nRewrite only those keys" + (", without these words: "
                     + ", ".join(banned[:30]) if banned else "")
                     + ". Say only what the notes say.")
            again, _ = llm.complete_json(retry, system=SYSTEM, models=models,
                                         max_tokens=900, temperature=0.2,
                                         thinking=False, attempts=2)
            if isinstance(again, dict):
                for key in refused:
                    if again.get(key):
                        drafted[key] = again[key]
                found = problems_of(drafted)
    except llm.LLMError as exc:
        failure = f"the model call failed ({exc})"

    answers = []
    for key, question in QUESTIONS.items():
        question = question.format(company=job.get("company") or "this company")
        text = " ".join(str(drafted.get(key) or "").split())
        if key in COMPOSED_ONLY:
            problems = ["composed only (see ADR 0027)"]
        elif failure and key not in found:
            problems = [failure]
        else:
            problems = found.get(key) or []
        if problems:
            answers.append(Answer(key, question, compose(key, profile, job, bullets),
                                  "composed", "from your own sentences; the drafted "
                                  "answer was refused: " + "; ".join(problems[:3])))
        else:
            answers.append(Answer(key, question, text, "model"))

    con.execute("UPDATE application_answers SET archived_at = ? "
                "WHERE application_id = ? AND archived_at IS NULL",
                (db.utcnow(), app["id"]))
    for a in answers:
        con.execute("INSERT INTO application_answers (application_id, question_key, "
                    "question, body, source, note, model) VALUES (?,?,?,?,?,?,?)",
                    (app["id"], a.key, a.question, a.body, a.source, a.note or None,
                     model or None))
    return answers


def stored(con: sqlite3.Connection, job_id: int) -> list[Answer]:
    rows = con.execute(
        "SELECT s.* FROM application_answers s JOIN applications a "
        "ON a.id = s.application_id WHERE a.job_id = ? AND s.archived_at IS NULL "
        "ORDER BY s.id", (job_id,)).fetchall()
    return [Answer(r["question_key"], r["question"], r["body"], r["source"],
                   r["note"] or "") for r in rows]


def job_row(con: sqlite3.Connection, job_id: int) -> dict[str, Any] | None:
    row = con.execute("SELECT j.*, c.name AS company FROM jobs j LEFT JOIN companies c "
                      "ON c.id = j.company_id WHERE j.id = ?", (job_id,)).fetchone()
    return dict(row) if row else None

