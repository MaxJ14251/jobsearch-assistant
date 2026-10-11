"""Who each sentence is about (plan 37, ADR 0035). Pure, no model.

The word check (letter.unsupported_words) treats every word of a draft as a
claim about the person, so "Vercel ships developer tools" fails on words
that describe Vercel. This module splits a draft into sentences and sorts
each one:

- **employer**: positively about the employer or the role ("Vercel ships…",
  "This role focuses on…", "Your team…"). Its words may also come from the
  posting the drafter was given.
- **strict**: everything else. About the person, mixed, or unclear: held to
  today's check, unchanged.

Every doubt goes to strict. A sentence with any first-person word, any word
standing in for the person ("candidate", "someone", their own name) or a
self-referring opener ("Having shipped…") is strict, whatever else it says.
A sentence counts as about the employer only when it STARTS with an
employer subject; anything else is unclear, and unclear is strict.

One narrow exception, `ATTITUDE_FRAME`: "I'm drawn to Vercel's focus on
developer experience" is an attitude toward the employer, not a claim of
skill. The frame words are a fixed list, and the rest of the sentence is
checked as an employer sentence only when it starts with an employer
subject and carries no clause that could say something about the person
("…, which I led", "…because I've run it").
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ATTITUDE_FRAME = True

FIRST_PERSON = frozenset("""
i i'm i've i'd i'll im ive me my mine myself we we're we've we'd we'll us our ours
ourselves let's lets
""".split())

# Words that stand in for the person without saying "I".
STAND_INS = frozenset("""
candidate candidates applicant applicants someone somebody whoever yours applying hire
""".split())
STAND_IN_PHRASES = ("a person", "the writer", "this hire", "yours truly",
                    "the right person", "a person who")

# Openers that describe the person before naming anyone else:
# "Having shipped developer tools, Vercel's mission fits."
SELF_OPENERS = ("having", "as someone", "as a", "as an", "after years", "since joining",
                "with years", "coming from")

LEADING = ("and", "also", "plus", "at", "today", "right now", "currently")

ROLE_SUBJECTS = ("the role", "this role", "the position", "this position", "the team",
                 "this team", "the company", "this company", "the job", "this job",
                 "your", "you")
PRONOUN_SUBJECTS = ("its", "their", "they", "it")

FRAMES = ("i'm drawn to", "i am drawn to", "i admire", "i like", "i appreciate",
          "i'm excited by", "i am excited by", "i'm interested in", "i am interested in",
          "what draws me to", "what excites me about")

# In the rest of a frame sentence, any of these could start a clause about
# the person ("I admire Vercel's team, which I led").
FRAME_BREAKERS = frozenset("""
and because since which who whom whose where while as having after so when
""".split())


@dataclass
class Sentence:
    text: str            # the sentence as written
    kind: str            # "employer" or "strict"
    why: str             # why it was sorted so
    body: str            # what the employer check reads (the whole sentence,
                         # or the part after an attitude frame)


_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")


def split(text: str) -> list[str]:
    """Sentences: on . ! ? ; followed by space or end, and on line breaks.
    Commas, dashes and colons never split, so a clause stays with its
    sentence. A wrong split only makes fragments with no employer subject,
    which are strict."""
    return [s.strip() for s in _SPLIT.split(str(text or "")) if s and s.strip()]


def tokens(text: str) -> list[str]:
    """Lowercase words, apostrophes kept, short words too ("i", "my", "us")."""
    text = str(text or "").lower().replace("’", "'").replace("‘", "'")
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text)


def _norm(text: str) -> str:
    return " ".join(tokens(text))


def _starts(words: list[str], phrase: str) -> int:
    """How many tokens `phrase` covers at the start of `words`, or 0."""
    want = phrase.split()
    if words[:len(want)] == want:
        return len(want)
    return 0


def _skip_leading(words: list[str]) -> list[str]:
    changed = True
    while changed and words:
        changed = False
        for lead in LEADING:
            n = _starts(words, lead)
            if n:
                words = words[n:]
                changed = True
    return words


def _company_words(company: str | None) -> list[str]:
    return tokens(company or "")


def _employer_start(words: list[str], company: str | None, previous_employer: bool) -> bool:
    """Whether these words start with something that is the employer or the role."""
    words = _skip_leading(list(words))
    if not words:
        return False
    name = _company_words(company)
    if name:
        head = words[:len(name)]
        if len(head) == len(name):
            last = head[-1]
            if last.endswith("'s"):
                head = head[:-1] + [last[:-2]]
            if head == name:
                return True
    for subject in ROLE_SUBJECTS:
        if _starts(words, subject):
            return True
    if previous_employer:
        for subject in PRONOUN_SUBJECTS:
            if _starts(words, subject):
                return True
    return False


def _person_names(person: list[str] | None) -> set[str]:
    out: set[str] = set()
    for name in person or []:
        out |= {w for w in tokens(name) if len(w) > 1}
    return out


def _disqualified(words: list[str], person: set[str]) -> str:
    """Why this text says something about the person, or ""."""
    for w in words:
        if w in FIRST_PERSON:
            return f"first person ({w!r})"
        if w in STAND_INS:
            return f"stands in for the person ({w!r})"
        base = w[:-2] if w.endswith("'s") else w
        if base in person:
            return "names the person"
    joined = " " + " ".join(words) + " "
    for phrase in STAND_IN_PHRASES:
        if f" {phrase} " in joined:
            return f"stands in for the person ({phrase!r})"
    rest = _skip_leading(list(words))
    for opener in SELF_OPENERS:
        if _starts(rest, opener):
            return f"opens about the person ({opener!r})"
    return ""


def _frame(sentence: str, company: str | None, person: set[str],
           previous_employer: bool) -> str | None:
    """The part after an attitude frame, if the sentence is one we accept."""
    lowered = sentence.lower().replace("’", "'")
    words = tokens(lowered)
    for frame in FRAMES:
        n = _starts(words, frame)
        if not n:
            continue
        rest = words[n:]
        if not rest or not _employer_start(rest, company, previous_employer):
            return None
        if _disqualified(rest, person):
            return None
        if FRAME_BREAKERS & set(rest) or "," in sentence:
            return None
        if "that i" in " ".join(rest):
            return None
        # The text after the frame words, as written.
        match = re.match(r"\s*" + r"\W+".join(re.escape(w) for w in frame.split()) + r"\b",
                         lowered)
        return sentence[match.end():].strip() if match else " ".join(rest)
    return None


def sort(text: str, company: str | None = None, person: list[str] | None = None,
         attitude_frame: bool | None = None) -> list[Sentence]:
    """Every sentence of `text`, sorted. `person` is the person's own names
    (full and preferred), which in a sentence make it about them."""
    use_frame = ATTITUDE_FRAME if attitude_frame is None else attitude_frame
    names = _person_names(person)
    out: list[Sentence] = []
    previous_employer = False
    for sentence in split(text):
        words = tokens(sentence)
        if use_frame:
            rest = _frame(sentence, company, names, previous_employer)
            if rest is not None:
                out.append(Sentence(sentence, "employer", "an attitude toward the employer",
                                    rest))
                previous_employer = True
                continue
        reason = _disqualified(words, names)
        if reason:
            out.append(Sentence(sentence, "strict", reason, sentence))
            previous_employer = False
            continue
        if _employer_start(words, company, previous_employer):
            out.append(Sentence(sentence, "employer", "starts with the employer or role",
                                sentence))
            previous_employer = True
            continue
        out.append(Sentence(sentence, "strict", "unclear who it is about", sentence))
        previous_employer = False
    return out


NEGATIONS = frozenset("not no never without lacks lack lacking none nor cannot".split())


def negated(text: str) -> bool:
    return any(w in NEGATIONS or w.endswith("n't") for w in tokens(text))


def numbers(text: str) -> list[str]:
    return re.findall(r"\d[\d,.]*\d|\d", str(text or ""))
