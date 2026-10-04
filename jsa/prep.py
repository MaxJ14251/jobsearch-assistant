"""Interview preparation generated from a specific posting.

Two questions arrive in nearly every screen for this candidate, and both are
answerable well — but only if the answer is prepared rather than improvised:

  * The employment gap since November 2023.
  * The degree that was never conferred, against the 57% of matched postings
    that state a degree requirement.

The second is where a model will quietly do damage. Asked to write a confident
answer, it reaches for fluent phrasing — "my degree in Computer Science",
"after I graduated" — which is natural, persuasive and false. `DEGREE_DENY`
below is checked against every generated answer, and `tests/test_prep.py`
proves the check works.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from . import db, llm, posting
from .tailor import collect_bullets, scrub_prompt, term_pattern

# Phrasings that assert a conferred degree. Checked case-insensitively against
# every generated answer. Fluent and false is the failure mode being caught.
# How much of a posting the model is shown. One rule, in jsa/posting.py.
MAX_DESCRIPTION_CHARS = posting.MAX_DESCRIPTION_CHARS

DEGREE_DENY = [
    "my degree in",
    "my degree from",
    "graduated with",
    "graduated from",
    "i graduated",
    "when i graduated",
    "after graduating",
    "i hold a degree",
    "i have a degree",
    "i earned a degree",
    "earned my degree",
    "i hold a bachelor",
    "i have a bachelor",
    "my bachelor's",
    "my bachelors",
    "completed my degree",
    "finished my degree",
    "received my degree",
    "obtained my degree",
    "bs in computer science",
    "b.s. in computer science",
    "phd",
    "doctorate",
    "my alma mater",
]


# The stages an inbox reply or a person can move an application into that
# are also prep rounds. At these, the tool SUGGESTS prep; it never runs it
# and never sets a next action (ADR 0021: the tool suggests, you confirm).
INTERVIEW_ROUNDS = ("phone_screen", "technical", "onsite")


def suggestion(con: sqlite3.Connection, application_id: int, stage: str) -> str | None:
    """What to say when an application reaches `stage`. Reads only."""
    if stage not in INTERVIEW_ROUNDS:
        return None
    row = con.execute(
        "SELECT id FROM interview_prep WHERE application_id = ? AND round = ? "
        "AND archived_at IS NULL ORDER BY id DESC LIMIT 1",
        (application_id, stage)).fetchone()
    if row is not None:
        return f"Prep for this round exists: `jsa prep-show {row['id']}`"
    return (f"Interview stage: draft prep with "
            f"`jsa prep {application_id} --round {stage}`")


class DegreeClaimError(RuntimeError):
    """Generated prep implied a credential that was never awarded."""


@dataclass
class Question:
    question: str
    why: str
    answer_notes: str


@dataclass
class Prep:
    application_id: int
    round: str
    questions: list[Question] = field(default_factory=list)
    company_brief: str = ""
    model: str = ""
    # Which claims were drilled: the resume that went out, or the whole profile
    # when nothing is recorded as sent. Said out loud so it is never assumed.
    drilled_from: str = ""
    # A warning when the posting has too little text to write role-specific
    # questions from (Plan 5). About this run only, so it is not stored.
    thin_note: str = ""


# A literal list cannot keep up with conjugation: "obtained my degree" was
# listed, "obtaining my degree" was not, and the second sailed through. These
# patterns cover the verb families instead of individual tenses.
DEGREE_PATTERNS = [
    # earn / obtain / receive / complete / finish / hold / have / get + a degree
    re.compile(
        r"\b(earn|obtain|receiv|complet|finish|hold|hav|get|got|attain)\w*\s+"
        r"(my|a|an|the|his|her|their)\s+"
        r"(degree|bachelor\w*|bs\b|b\.s\.|ba\b|masters?\b|phd|doctorate)",
        re.I,
    ),
    # any form of "graduate" used of the speaker
    re.compile(r"\b(i\s+)?graduat\w*\b", re.I),
    # possessive degree claims
    re.compile(r"\bmy\s+(degree|bachelor\w*|masters?|phd|doctorate|alma mater)\b", re.I),
    # credentials never held at all
    re.compile(r"\b(phd|ph\.d|doctorate)\b", re.I),
    re.compile(r"\b(b\.?s\.?|bachelor'?s?)\s+in\s+computer\s+science\b", re.I),
]


# The honest framing necessarily mentions a degree — "I did NOT finish the
# degree", "the degree was never conferred". A match preceded by a negation is
# a denial, which is exactly what we want the text to say.
_NEGATION = re.compile(
    r"\b(not|never|n't|no|without|didn|couldn|haven|hasn|unfinished|"
    r"incomplete|short of)\b[\s\w,'’]{0,40}$"
)
_NEGATION_WINDOW = 60


def _is_negated(haystack: str, start: int) -> bool:
    return bool(_NEGATION.search(haystack[max(0, start - _NEGATION_WINDOW):start]))


def assert_no_degree_claim(text: str) -> str:
    """Raise if the text ASSERTS a conferred degree.

    Two refinements, both found by tests:

    * A literal phrase list cannot keep up with tense — "obtained my degree"
      was listed, "obtaining my degree" was not, and it passed. Hence the
      verb-family patterns.
    * Those patterns then flagged the honest answer, because "I did not finish
      the degree" contains "finish the degree". A match preceded by a negation
      is a denial, not a claim, so it is allowed through.
    """
    lowered = " ".join((text or "").lower().split())

    for phrase in DEGREE_DENY:
        index = lowered.find(phrase)
        if index != -1 and not _is_negated(lowered, index):
            raise DegreeClaimError(
                f"generated prep contains {phrase!r}, which implies a degree "
                "that was not conferred; the honest framing is coursework "
                "completed in the field"
            )

    for pattern in DEGREE_PATTERNS:
        for match in pattern.finditer(lowered):
            if not _is_negated(lowered, match.start()):
                raise DegreeClaimError(
                    f"generated prep contains {match.group(0)!r}, which implies "
                    "a degree that was not conferred; the honest framing is "
                    "coursework completed in the field"
                )
    return text


def degree_answer(profile: dict[str, Any]) -> str:
    """The prepared answer, built from the profile's credential line rather
    than generated (jsa/facts.py). It used to say "did not finish" whatever
    the profile said, which was wrong for anyone who had."""
    from .facts import degree_answer as from_profile
    return from_profile(profile)


def gap_answer(profile: dict[str, Any]) -> str | None:
    """From the profile's own dates; None when there is no gap to explain."""
    from .facts import gap_answer as from_profile
    return from_profile(profile)


def standard_drills(profile: dict[str, Any]) -> list[Question]:
    """The degree question always; the gap question when there is a gap."""
    from .facts import gap_since

    drills = []
    gap = gap_since(profile)
    if gap is not None:
        drills.append(Question(
            question=f"Can you walk me through the gap since {gap[0]}?",
            why="Any reader of the resume will notice it; being unprepared "
                "here reads as evasion rather than a deliberate pivot.",
            answer_notes=gap_answer(profile) or "",
        ))
    drills.append(Question(
        question="Tell me about your educational background.",
        why="57% of matched postings state a degree requirement. This "
            "answer must be identical every time and must never imply a "
            "degree the profile doesn't state.",
        answer_notes=degree_answer(profile),
    ))
    return drills


SYSTEM = (
    "You write interview preparation. Every question must be grounded in the "
    "job description you are given — quote or paraphrase the line that prompts "
    "it. Answer notes may use ONLY the candidate facts supplied. Never invent "
    "experience, credentials, metrics or employers. Output JSON only."
)

PROMPT = """Generate interview questions for this role.

Return JSON: {{"questions": [{{"question": "...", "why": "the JD line that prompts this",
"answer_notes": "first person, what I should SAY, from the facts below only"}}],
"company_brief": "3 sentences on what this team appears to do, from the posting"}}

Produce 5 questions for a {round} round.

Write answer_notes in the FIRST PERSON, as words I could say out loud -- "I
built...", "I have not worked with X, but...". The two standard drills in this
document are already written that way and a reader switching between "I built"
and "the candidate built" halfway down has to translate under pressure. Where
I lack something the role wants, say so plainly in the first person and name
what I would draw on instead. Do not invent the experience.

JOB TITLE: {title}
COMPANY: {company}
POSTING:
{description}

CANDIDATE FACTS — the only material available for answers:
{bullets}

The candidate completed coursework in {field} but the degree was NOT conferred.
Never write that they hold, earned or graduated with a degree."""


def sent_bullets(
    con: sqlite3.Connection, application_id: int, profile: dict[str, Any],
) -> tuple[list[Any], str]:
    """The bullets the interviewer has in front of them, and where that came from.

    An interviewer reads the resume you sent, not your profile and not the
    newest draft. When submitted_documents names one, prep drills exactly its
    bullets. Otherwise it falls back to the whole profile and says so.
    """
    import json

    from . import approvals

    everything = collect_bullets(profile)
    for item in approvals.submitted(con, application_id):
        if item.kind != "resume":
            continue
        raw = con.execute("SELECT bullet_ids FROM documents WHERE id = ?",
                          (item.document_id,)).fetchone()
        ids = json.loads(raw["bullet_ids"] or "[]") if raw else []
        chosen = [everything[i] for i in ids if i in everything]
        if chosen:
            return chosen, f"the resume you sent (doc {item.document_id} v{item.version})"
        return list(everything.values()), (
            f"the whole profile: the resume you sent (doc {item.document_id}) "
            "has no bullet record this profile still recognises")
    return list(everything.values()), (
        "the whole profile: no resume is recorded as sent for this application")


def generate(
    con: sqlite3.Connection, application_id: int, *, round: str = "phone_screen",
    profile: dict[str, Any] | None = None, models: list[str] | None = None,
) -> Prep:
    from .config import load_profile

    profile = profile or load_profile()
    row = con.execute(
        """SELECT a.id AS app_id, j.id AS job_id, j.title, j.description, j.degree_required,
                  c.name AS company, c.research_notes
             FROM applications a
             JOIN jobs j ON j.id = a.job_id
             JOIN companies c ON c.id = j.company_id
            WHERE a.id = ?""",
        (application_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"no application with id {application_id}")

    edu = (profile.get("education") or [{}])[0]
    bullets, drilled_from = sent_bullets(con, application_id, profile)
    prompt = PROMPT.format(
        round=round.replace("_", " "),
        title=row["title"], company=row["company"],
        description=posting.visible(row["description"]).text,
        bullets="\n".join(f"- {b.text}" for b in bullets),
        field=edu.get("field") or "their field",
    )
    scrub_prompt(prompt, profile)

    data, usage = llm.complete_json(
        prompt, system=SYSTEM, models=models, max_tokens=1600,
        temperature=0.3, thinking=False, attempts=2,
    )

    questions = list(standard_drills(profile))
    for item in (data.get("questions") or []) if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        q = Question(
            question=str(item.get("question") or "").strip(),
            why=str(item.get("why") or "").strip(),
            answer_notes=str(item.get("answer_notes") or "").strip(),
        )
        if q.question:
            questions.append(q)

    brief = str((data or {}).get("company_brief") or "") if isinstance(data, dict) else ""

    # Every generated word is checked before it is stored.
    for q in questions:
        assert_no_degree_claim(q.answer_notes)
        assert_no_degree_claim(q.question)
    assert_no_degree_claim(brief)

    prep = Prep(application_id=application_id, round=round,
                questions=questions, company_brief=brief, model=usage.model,
                drilled_from=drilled_from,
                thin_note=posting.thin(row["description"], row["job_id"],
                                       for_what="prep"))
    save(con, prep)
    return prep


def save(con: sqlite3.Connection, prep: Prep) -> int:
    """Insert a new row. Prior prep is never overwritten."""
    cur = con.execute(
        "INSERT INTO interview_prep (application_id, round, questions, "
        "company_brief) VALUES (?,?,?,?)",
        (prep.application_id, prep.round,
         json.dumps([q.__dict__ for q in prep.questions]), prep.company_brief),
    )
    return int(cur.lastrowid)
