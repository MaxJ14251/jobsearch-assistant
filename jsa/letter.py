"""Cover letters. See docs/decisions/0007-*.

A letter needs sentences a resume does not have -- "I am applying for...",
"I would welcome the chance to talk" -- and none of them exist in the profile.
Composing one from verified sentences alone produced exactly what you would
expect: the summary and three bullets in a row, no greeting, no closing, no
letter. So a second model call writes the prose, and everything it writes is
held to one rule:

    every meaningful word must come from the candidate's own profile, from a
    short fixed list of connective words, or from the role title, company and
    location -- which are database columns, not model output.

The posting's words are deliberately NOT allowed. ADR 0005 records what
happened when the resume prompt was told to borrow the posting's vocabulary:
"strict turnaround requirements", "SLA adherence", "motion-control software".
The same rule also keeps flattery about the employer out, because "industry-
leading" is not in the profile either.

Three checks sit on top of the vocabulary rule, because word overlap cannot
see them: the degree (never conferred), work still in development (never
described as finished), and the operator's banned-term list.

A letter that fails is not refused. It falls back to a deterministic letter
built from verified sentences with proper scaffolding, and the document
records which path produced it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import llm, posting
from .tailor import (
    MAX_DESCRIPTION_CHARS, TailoredDraft, _stem, _whole_profile_stems, _words,
    do_not_claim, scrub_prompt, term_pattern,
)

ATTEMPTS = 2
MIN_WORDS = 120
MAX_WORDS = 350

# The words a letter may use that say nothing about the candidate.
#
# THE RULE FOR THIS LIST: no entry may name a skill, a tool, an outcome or a
# quality. "communication", "leadership", "fast-paced" and every technology
# stay out on purpose -- those are claims, and the profile is where claims
# come from. Ordinary English is not a claim: the first measured letter was
# rejected for "spent", "gave" and "every", which say nothing about anyone.
CONNECTIVES = frozenset("""
applying application apply applicant candidate role position opening posting
job team company hiring manager dear sincerely regards thank thanks
consider consideration welcome opportunity chance conversation discuss talk
speak interview available availability attached resume letter below above
interested interest excited drawn keen looking forward hope hear
bring offer contribute help join work working recently currently
please reach contact respond reply time attention
my your our me you they them who what where when how why
because since although however therefore what's why's
about after before during under over between beyond within without
would could should will shall can may might must
have has had having do does did doing get got make made making
want need know think feel see saw look looks
year years month months week weeks day days
one two three four five six seven eight nine ten
best kind sincerely yours truly

spend spent spends give gave gives given take took taken taking
put puts keep kept turn turned turns move moved start started begin began
use uses used using apply applied applies combine combined combines
learn learned learnt learning teach taught show showed shown
mean means meant matter matters fit fits fitted suit suits
every each both either any all some most many much few several other others
current currently now today recent recently already still yet again
here there this that these those such same different next last first second
where when while during after before between across along toward towards
side kind sort type way ways range mix combination background history
field area part parts piece step steps point points thing things
place places home office remote onsite hybrid shift weekend
role roles job jobs task tasks day-to-day
into onto from with without within among against around behind beside
than then though although because since so but and or nor if unless until
very quite rather enough less least more most too also just only even
am are is was were been being do did done goes going went gone
say says said tell told ask asked answer answered
read reads write writes wrote written send sends sent
made make makes making meet meets met
align aligns aligned match matches matched relate relates related
translate translates reflect reflects involve involves involved
draw draws drawn carry carries carried brought focus focuses focused
""".split())

_DEGREE = re.compile(
    r"\b(degree|degrees|bachelor'?s?|bachelors|master'?s?|masters|b\.?s\.?|"
    r"b\.?a\.?|m\.?s\.?|phd|ph\.?d\.?|doctorate|graduated|graduate|"
    r"diploma|alumnus|alumna|alum)\b",
    re.I,
)

SYSTEM = (
    "You write short, plain cover letters. You may only use facts the "
    "candidate's own notes contain. You never add a skill, outcome, quality "
    "or opinion that is not in those notes, and you never describe the "
    "company beyond its name and the role title. An automated check compares "
    "every word you write against the candidate's notes and rejects the "
    "letter otherwise."
)

PROMPT = """Write a cover letter for this role, in the first person.

STRUCTURE
- "Dear Hiring Manager," on its own line.
- A first paragraph saying which role at which company this is for, in one
  sentence, and one sentence of why -- using only what the notes below say.
- One or two short paragraphs drawing on the notes. Use at most FOUR notes --
  the ones that matter for THIS role -- and leave the rest out. Say what the
  candidate has actually done. Do not repeat a note word for word; join the
  facts into sentences. No sentence longer than 30 words.
- A closing line offering to talk, then "Sincerely," and nothing after it.

RULES
- Use only the words and facts in the notes. Nothing about the company beyond
  its name and this role's title.
- Work described as ongoing stays ongoing. "Building" does not become "built".
  A line beginning "Goal:" is an aim, not a result.
- Never mention a degree, a graduation, or years of experience.
- No adjectives about the employer, the industry or the product.
- 150 to 250 words, in total.

ROLE: {title}
COMPANY: {company}
LOCATION: {location}

NOTES ON THE CANDIDATE (the only facts you may use):
{summary}

{bullets}

WHAT THE ROLE INVOLVES (context for choosing which notes to use -- do NOT
copy its wording into the letter):
{description}

Return the letter as plain text. No preamble, no explanation."""


@dataclass
class LetterResult:
    body: str
    source: str                      # "model" or "composed"
    note: str = ""                   # why it fell back, or ""
    problems: list[str] = field(default_factory=list)
    model: str = ""


def identity_words(profile: dict[str, Any]) -> set[str]:
    """The operator's own name, contact details and links.

    render.py prints these in the letterhead and signs with the name, so a
    check run over the whole document saw them. They are the operator's own
    profile, not invention: a letter signed by its writer is not a claim.
    Outreach learned the same thing (ADR 0010).
    """
    ident = profile.get("identity") or {}
    parts = [str(ident.get(k) or "") for k in
             ("full_name", "preferred_name", "email", "phone")]
    location = ident.get("location") or {}
    parts += [str(v or "") for v in location.values()]
    parts += [str(v or "") for v in (profile.get("links") or {}).values()]
    return _words(" ".join(parts))


def allowed_stems(profile: dict[str, Any], job: dict[str, Any]) -> set[str]:
    """Every stem a letter may use."""
    stems = set(_whole_profile_stems(profile))
    stems |= {_stem(w) for w in CONNECTIVES}
    stems |= {_stem(w) for w in identity_words(profile)}
    for key in ("title", "company", "location"):
        stems |= {_stem(w) for w in _words(str(job.get(key) or ""))}
    return stems


def unsupported_words(text: str, profile: dict[str, Any],
                      job: dict[str, Any]) -> list[str]:
    """The letter's own words that nothing supports."""
    allowed = allowed_stems(profile, job)
    return sorted({w for w in _words(text) if _stem(w) not in allowed})


def ongoing_verbs(profile: dict[str, Any]) -> set[str]:
    """Finished forms of the verbs that describe unfinished work.

    "Building a Python pipeline" may not become "Built"; "Goal: reduce" may
    not become "Reduced". A stem test cannot see this -- reduce and reduced
    share a stem -- so the forms are listed explicitly.
    """
    from .tailor import _IRREGULAR

    past_of = {v: k for k, v in _IRREGULAR.items()}
    banned: set[str] = set()
    for project in profile.get("projects") or []:
        if project.get("status") == "complete":
            continue
        for bullet in project.get("bullets") or []:
            text = str(bullet.get("text", ""))
            lead = re.match(r"\s*([A-Za-z]+)", text)
            words = [lead.group(1)] if lead else []
            words += re.findall(r"\b([a-z]+)ing\b", text.lower())
            for word in words:
                base = word.lower().rstrip(":")
                if base == "goal" or len(base) < 4:
                    continue
                root = base[:-3] if base.endswith("ing") else base
                banned |= {root + "ed", root.rstrip("e") + "ed"}
                if root.endswith("e"):
                    banned.add(root + "d")          # "iterate" -> "iterated"
                if root in past_of:
                    banned.add(past_of[root])
    # A word the operator uses themselves is theirs to use: the profile says
    # "Architected closed-caption generation", so a letter may say it too.
    return banned - {w.lower() for w in _words(_profile_text(profile))}


def _profile_text(profile: dict[str, Any]) -> str:
    parts = []
    for section in ("experience", "projects"):
        for entry in profile.get(section) or []:
            for bullet in entry.get("bullets") or []:
                parts.append(str(bullet.get("text", "")))
    for summary in profile.get("summaries") or []:
        parts.append(str(summary.get("text", "")))
    return " ".join(parts)


def check(text: str, profile: dict[str, Any], job: dict[str, Any]) -> list[str]:
    """Everything wrong with this letter. Empty means it may be sent."""
    problems: list[str] = []
    words = text.split()
    if not re.search(r"\bdear\b", text, re.I):
        problems.append("no greeting")
    if not re.search(r"\b(sincerely|regards|best)\b", text, re.I):
        problems.append("no closing")
    title = str(job.get("title") or "").strip()
    company = str(job.get("company") or "").strip()
    if title and title.lower().split(" (")[0] not in text.lower():
        problems.append(f"does not name the role ({title})")
    if company and company.lower() not in text.lower():
        problems.append(f"does not name the company ({company})")
    if not MIN_WORDS <= len(words) <= MAX_WORDS:
        problems.append(f"{len(words)} words, outside {MIN_WORDS}-{MAX_WORDS}")

    match = _DEGREE.search(text)
    if match:
        problems.append(f"mentions a degree ({match.group(0)!r})")

    finished = ongoing_verbs(profile)
    said = {w.lower().strip(".,;:") for w in words}
    for verb in sorted(finished & said):
        problems.append(f"describes ongoing work as finished ({verb!r})")

    for term in do_not_claim(profile):
        if term and term_pattern(term).search(text.lower()):
            problems.append(f"claims {term!r}, which is on your do-not-claim list")

    new = unsupported_words(text, profile, job)
    if new:
        problems.append("says words your profile does not: " + ", ".join(new[:8]))
    return problems


def compose(draft: TailoredDraft, profile: dict[str, Any],
            job: dict[str, Any]) -> str:
    """A letter built only from verified sentences. Always honest, never prose.

    The fallback when the model's letter fails a check, and the reason a
    failure never leaves the operator with nothing to send.
    """
    ident = profile.get("identity") or {}
    title = str(job.get("title") or "the role").split(" (")[0]
    company = str(job.get("company") or "your team")
    lines = [
        "Dear Hiring Manager,",
        f"I am applying for the {title} role at {company}.",
        " ".join(draft.summary.split()),
    ]
    lines += [" ".join(b.text.split()) for b in draft.bullets[:3]]
    lines += [
        "I would welcome the chance to talk about the role.",
        "Sincerely,",
        str(ident.get("full_name") or "").strip(),
    ]
    return "\n\n".join(line for line in lines if line)


def write(job: dict[str, Any], profile: dict[str, Any], draft: TailoredDraft,
          *, models: list[str] | None = None) -> LetterResult:
    """A checked letter. Falls back to composed text rather than failing."""
    notes = "\n".join(f"- {b.text}" for b in draft.bullets)
    prompt = PROMPT.format(
        title=job.get("title") or "the role",
        company=job.get("company") or "the company",
        location=job.get("location") or "not stated",
        summary=" ".join(draft.summary.split()),
        bullets=notes,
        description=posting.visible(job.get("description")).text,
    )
    scrub_prompt(prompt, profile)       # fails closed before any network call

    last: list[str] = []
    model = ""
    attempt_prompt = prompt
    for attempt in range(ATTEMPTS):
        try:
            result = llm.complete(
                attempt_prompt, system=SYSTEM, models=models, max_tokens=700,
                temperature=0.4 if attempt == 0 else 0.2, thinking=False,
                purpose="letter",
            )
        except Exception as exc:  # noqa: BLE001 - a failed call falls back
            last = [f"the model call failed ({type(exc).__name__})"]
            break
        model = result.usage.model or ""
        text = "\n".join(line.strip()
                         for line in (result.text or "").strip().splitlines())
        problems = check(text, profile, job)
        if not problems:
            return LetterResult(body=text, source="model", model=model)
        last = problems
        # Say what failed rather than re-rolling blind. One honest word out of
        # place ("communication" in a letter that is otherwise entirely the
        # candidate's own material) should cost a rewrite, not the letter.
        banned = unsupported_words(text, profile, job)
        attempt_prompt = prompt + "\n\nYour previous draft was rejected: " + \
            "; ".join(problems[:3]) + ".\nRewrite it. "
        if banned:
            attempt_prompt += ("Do not use any of these words: "
                               + ", ".join(banned) + ". ")
        attempt_prompt += "Say only what the notes say."

    return LetterResult(
        body=compose(draft, profile, job), source="composed", model=model,
        note="written from your own sentences; the drafted prose was refused: "
             + "; ".join(last[:3]),
        problems=last,
    )
