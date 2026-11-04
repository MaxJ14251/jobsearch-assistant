"""What one Suggest press is about: the question and this listing. Pure, no model.

Three things drawn from the same text gave the same answer to every question
at every employer (ADR 0034 note, 2026-10-31): the first 2,000 characters of a
posting are mostly the company's blurb, the question chose nothing, and every
option drew on the same bullets. So, before any model call:

- `kind` says what is being asked, so the prompt can ask for that shape.
- `excerpt` picks the posting's lines by the question: the role and what it
  asks for, the blurb only when the question is about the employer. Pay, perks
  and legal text never go. The check reads the same text, so sentences about
  the employer may use exactly what the model was shown.
- `assign` gives each option notes of its own, from a different project or job
  where there is one, so options can't share facts. It goes by the bullets the
  job's resume uses and by the question, not by words in common with the posting:
  tried first, that paired a posting's Node.js line with a sales bullet from
  an unrelated job because both said "security" and "solutions". Which requirement a note
  speaks to takes judgement, so the model picks it from the excerpt.

Nothing here adds a word an answer may use, or decides anything about the person.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .posting import OUTREACH_CHARS, visible
from .tailor import _stem, _words

_ME = r"(you|your|you're|you've)"
_US = r"(us|our|here|this company|the company|{company})"

# First match wins: "Why do you want this role at Figma?" is about the role.
_KINDS = (
    ("why_role", r"\bwhy\b.*\b(role|position|job|opportunity)\b"
                 r"|\b(excites?|interests?|draws?|appeals?)\b.*\b(role|position|job|opportunity)\b"),
    # "working": Riot's "why are you interested in working at Riot Games as a
    # Software Engineering Intern?" read as an open question (2026-11-04).
    ("why_company", r"\bwhy\b.*\b(work|working|join|joining|apply|applying)\b"
                    r"|\bwhy\s+" + _US + r"\b"
                    r"|\b(excites?|interests?|draws?|appeals?)\b.*\b" + _US + r"\b"
                    r"|\b(work|join)\b.*\b" + _US + r"\b.*\bwhy\b"),
    # Asks what shaped a piece of work, not only for one: "Describe an example of
    # an agent and the considerations that shape its harness" (2026-11-03). Only
    # about work the candidate did: "any deadlines or timeline considerations?"
    # is a question about logistics.
    ("explain", r"(?=.*\b(describe|explain|walk|tell|share|example|you built|you made"
                r"|did you|you faced)\b).*\b(considerations?|trade ?offs?|tradeoffs?"
                r"|design decisions?|approach(es)?|challenges?|lessons?|constraints?"
                r"|pitfalls?)\b"
                r"|\bhow (you|did you|would you) (approached|approach|designed|design|decided"
                r"|decide|handled|handle|solved|solve|chose|choose)\b"),
    ("skills", r"\b(technolog(y|ies)|tools?|languages?|frameworks?|tech stack|platforms?)\b"
               r".*\b" + _ME + r"\b|\b" + _ME + r"\b.*\b(technolog(y|ies)|tools?|languages?"
               r"|frameworks?|tech stack|platforms?)\b"),
    ("project", r"\b(projects?|achievements?|accomplishments?|proud|proudest|built|build"
                r"|shipped|example of|a time (when|you))\b"),
    ("experience", r"\b(experience|background|worked with|familiar)\b"),
)

_EDUCATION = re.compile(
    r"\b(highest (level of )?education|level of education|education level|highest degree"
    r"|what degree|do you (have|hold) a degree|education(al)? background)\b", re.I)


def is_education(question: str) -> bool:
    """Asked for a fact the profile holds word for word; nothing to draft."""
    return bool(_EDUCATION.search(question or ""))


# Leaves the subject to the candidate, whatever else it names: "Feel free to
# share any additional context ... or anything else you'd like us to know about
# your experience or interest in this role at LangChain" read as a
# why-this-role question (2026-11-04).
_OPEN_FIRST = re.compile(r"\b(anything else|additional (context|information|comments|details)"
                         r"|feel free to share|is there anything)\b")


def kind(question: str, company: str | None = None) -> str:
    """why_role, why_company, explain, skills, project, experience or open."""
    text = " ".join(re.sub(r"[^a-z0-9' ]+", " ", str(question or "").lower()
                           .replace("’", "'")).split())
    if _OPEN_FIRST.search(text):
        return "open"
    name = " ".join(re.sub(r"[^a-z0-9' ]+", " ", str(company or "").lower()).split())
    for found, pattern in _KINDS:
        if re.search(pattern.replace("{company}", re.escape(name) if name else "\0"), text):
            return found
    return "open"


# --- the posting's lines, sorted by what they are ---------------------------------

@dataclass(frozen=True)
class Line:
    text: str
    section: str         # company | role | wants | skip


_SECTIONS = (
    ("skip", r"benefit|perks?\b|compensation|salary|pay (range|transparency)|equal opportunit"
             r"|accommodation|disclosure|privacy|what to expect|important information"
             r"|interview|how and where we work|note on ai|candidates seeking|offer package"
             r"|learn more|^#"),
    ("wants", r"qualification|requirement|what you need|you (should|must) have|what you.?ll bring"
              r"|you.?ll bring|skills|good fit|looking for|who you are|nice to have"
              r"|bonus if|added plus|love to hear|you have|ideal candidate"),
    ("role", r"the role|this role|what you.?ll|what you will|you will|responsibilit"
             r"|your impact|day to day|the job|the opportunity|achieve"),
    ("company", r"^about\b|who (we are|are we)|our (mission|story)|why join|what we value"),
)
_SKIP_LINE = re.compile(
    r"https?://|\d,\d{3}\b|\d\s?(k|m|million|billion)\b|\b401\s?\(?k"
    r"|\bequal opportunity\b|\bsalary\b|\bdental\b"
    r"|\bbase pay\b|\bequity\b.*\bcompensation\b|\bcompensation\b.*\bequity\b", re.I)


def _heading(line: str) -> bool:
    """A line that names a section. A short line nothing recognizes ("Experience
    with SQL") is content, not a heading."""
    words = line.split()
    if not words or len(words) > 10 or line.startswith(("-", "•", "*")):
        return False
    if line.endswith((":", "?")) or line.upper() == line:
        return True
    return len(words) <= 6 and not line.endswith((".", ",", ";")) \
        and _section_of(line) is not None


def _section_of(heading: str) -> str | None:
    text = heading.lower().replace("’", "'")
    for section, pattern in _SECTIONS:
        if re.search(pattern, text):
            return section
    return None


def lines(description: str | None) -> list[Line]:
    """The posting line by line. Before any heading is company text when the
    posting opens with one paragraph; a heading nothing recognizes keeps the
    section it is in."""
    out: list[Line] = []
    section = "company"
    for raw in str(description or "").splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        if _heading(line):
            section = _section_of(line) or section
            continue
        # Sentence by sentence: "raised $150M" shouldn't cost the whole blurb.
        kept = " ".join(x for x in re.split(r"(?<=[.!?])\s+", line) if not _SKIP_LINE.search(x))
        if kept:
            out.append(Line(kept, section))
    # A posting that never says where the role starts: no way to sort it.
    if not any(l.section in ("role", "wants") for l in out):
        return []
    return out


COMPANY_SHARE = 400      # of the excerpt, when the question isn't about the employer


def _fits(text: str, room: int) -> str:
    """As many of a line's sentences as fit; never cut mid-sentence."""
    if len(text) + 1 <= room:
        return text
    kept: list[str] = []
    used = 0
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if used + len(sentence) + 1 > room:
            break
        kept.append(sentence)
        used += len(sentence) + 1
    return " ".join(kept)


def excerpt(description: str | None, question_kind: str = "open",
            budget: int = OUTREACH_CHARS) -> str:
    """The posting text for this question, in the posting's own order. With no
    sections to go by, the start of the posting, as before."""
    found = lines(description)
    if not found:
        return visible(description, budget=budget).text
    company_room = budget // 2 if question_kind == "why_company" else COMPANY_SHARE
    picked: dict[int, str] = {}
    used = 0
    # The role first, but leaving room for what it asks for; then whatever's left.
    for section, cap in (("company", company_room), ("role", budget * 55 // 100),
                         ("wants", budget), ("role", budget)):
        spent = 0
        for i, line in enumerate(found):
            if line.section != section or i in picked:
                continue
            text = _fits(line.text, min(cap - spent, budget - used))
            if not text:
                continue
            picked[i] = text
            spent += len(text) + 1
            used += len(text) + 1
    return "\n".join(picked[i] for i in sorted(picked)) \
        or visible(description, budget=budget).text


# --- notes of its own, per option ---------------------------------------------------

@dataclass(frozen=True)
class Material:
    angle: str
    notes: tuple[Any, ...]   # SourceBullets, all from one project or job


NOTES_PER_OPTION = 2     # a single bullet is often too short for the 40-word floor
_THIN = _words("experience work working ability strong skills including across using new "
               "team teams build building ensure help support years role job company "
               "looking great world well best high able etc various multiple "
               "tell about your you what how some")


def _stems(text: str) -> set[str]:
    return {_stem(w) for w in _words(text) if w not in _THIN}


def assign(angles: tuple[str, ...], bullets: list[Any], question: str,
           preferred: list[Any] | None = None) -> list[Material]:
    """One Material per angle while there is material: each from a project or
    job no other option uses. Parents the job's resume uses come first; a parent
    it doesn't use is used only to reach two options. When the question names
    something some of the work touches, only that work is used."""
    preferred = list(preferred or [])
    rank = {b.id: i for i, b in enumerate(preferred)}
    asked = _stems(question)

    def hits(b: Any) -> int:
        return len(asked & (_stems(b.text) | _stems(" ".join(b.tags))))

    parents: dict[str, list[Any]] = {}
    for b in bullets:
        parents.setdefault(b.parent, []).append(b)

    def weight(name: str) -> tuple[int, int, int]:
        members = parents[name]
        chosen = [rank[b.id] for b in members if b.id in rank]
        return (-sum(hits(b) for b in members), min(chosen, default=len(rank)), -len(chosen))

    def useful(name: str) -> bool:
        return any(b.id in rank or hits(b) for b in parents[name])

    order = sorted(parents, key=weight)
    left = [name for name in order if useful(name)]
    spare = [name for name in order if not useful(name)]
    # Asked about something specific ("closing enterprise deals"), work that
    # doesn't touch it isn't an answer, however good the rest of the resume is.
    # Weakest last, each only if it adds a word the stronger ones don't cover:
    # "closing deals" is sales work, not "closed-caption" work, while "clients
    # and agentic systems" is both a sales job and an agent project.
    touching: list[str] = []
    covered: set[str] = set()
    for name in order:
        found = {s for b in parents[name]
                 for s in asked & (_stems(b.text) | _stems(" ".join(b.tags)))}
        if found - covered:
            touching.append(name)
            covered |= found
    if touching:
        left, spare = touching, []
    out: list[Material] = []
    for angle in angles:
        wanted = left
        if angle == "project":
            wanted = [n for n in left if parents[n][0].origin == "project"] or left
        if not wanted:
            if len(out) >= 2 or not spare:
                break
            left = [spare.pop(0)]
            wanted = left
        name = wanted[0]
        left.remove(name)
        # What the resume uses, then what the question asks about; an aim
        # ("Goal: ...") is the weakest thing to build an answer on.
        notes = sorted(parents[name], key=lambda b: (b.text.lower().startswith("goal:"),
                                                     -hits(b), rank.get(b.id, len(rank)), b.id))
        out.append(Material(angle, tuple(notes[:NOTES_PER_OPTION])))
    return out
