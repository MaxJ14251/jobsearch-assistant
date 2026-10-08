"""What a posting asks for in education, read from its text with no model
(plan 32, ADR 0032).

A posting's stated requirement is not who gets hired: research by the
Burning Glass Institute and Harvard Business School found that dropping a
degree requirement often changes actual hiring little. Every surface that
shows these says so (`CAVEAT`).

Levels, from least to most asked:

    none                no degree mentioned for the candidate
    associate           an associate's degree, and nothing higher
    bachelors_or_equiv  a degree is asked for, but equivalent experience is
                        accepted, or it is only preferred
    bachelors           a bachelor's, required
    masters_preferred   a bachelor's required, and a graduate degree (master's,
                        MBA, PhD) preferred or offered as the alternative
    masters             a graduate degree required
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field

LEVELS = ("none", "associate", "bachelors_or_equiv", "bachelors", "masters_preferred",
          "masters")
LABELS = {
    "none": "no degree mentioned",
    "associate": "associate's degree",
    "bachelors_or_equiv": "bachelor's or equivalent experience",
    "bachelors": "bachelor's required",
    "masters_preferred": "bachelor's required, graduate degree preferred",
    "masters": "graduate degree required",
}
CAVEAT = ("What postings ask for, not who gets hired: dropping a degree requirement "
          "often changes actual hiring little.")
MAX_EVIDENCE = 160

# --- the phrases ------------------------------------------------------------------

# A degree the candidate is asked for. "BS"/"BA"/"MS" only as whole words
# next to a degree-ish context word, so "MS Office" and "BA team" don't count.
_BACHELOR = re.compile(
    r"\bbachelor'?s?\b|\bbaccalaureate\b|\bb\.\s?[sa]\.|\bb\.?sc\b|"
    r"\b(?:bs|ba)\b(?=\s*(?:/|or\b|and\b|degree|in\b|,|\(|preferred|required|is\b))|"
    r"\b(?:4|four)[- ]year (?:college |university )?degree\b|"
    r"\b(?:college|university|undergraduate) degree\b|"
    # "Degree in Computer Science", "a degree or diploma in ...": a first
    # degree in a field, the bachelor's in all but name.
    r"\bdegree (?:or diploma )?in\b|\bdegree-seeking\b|"
    r"\b(?:pursuing|enrolled in|working towards?) (?:a |an )?(?:\w+ ){0,3}degree\b", re.I)
_GRADUATE = re.compile(
    r"\bmaster'?s?\b(?! (?:service|data|agreement|class))|\bm\.\s?s\.|\bmba\b|\bm\.?sc\b|"
    r"\bms\b(?=\s*(?:/|or\b|and\b|degree|in\b|,|\(|preferred|required|is\b))|"
    r"\bph\.?\s?d\b|\bdoctor(?:ate|al)\b|"
    # Professional degrees are graduate degrees: medicine, law.
    r"\bmedical degree\b|\bm\.?d\.?(?=\s*(?:,|\)|/|or\b))|\bjuris doctor\b|"
    r"\bj\.?d\.?(?=\s*(?:,|\)|/|or\b|degree))|\blaw degree\b|"
    r"\b(?:graduate|advanced|post-?graduate) degree\b", re.I)
_ASSOCIATE = re.compile(r"\bassociate'?s?\s+(?:degree|of)\b|\btwo[- ]year degree\b", re.I)
# A named non-bachelor degree with its "degree in" tail, removed before the
# bachelor's test so "degree in" isn't counted twice.
_NAMED_DEGREE_IN = re.compile(
    r"(?:\bmaster'?s?|\bmba|\bph\.?\s?d|\bdoctor(?:ate|al)|\bassociate'?s?|"
    r"\btwo[- ]year|\bgraduate|\badvanced|\bmedical|\blaw)\s+(?:degree\s*)?(?:or diploma\s+)?"
    r"(?:in\b)?", re.I)
# Any degree word at all, for the negations below.
_ANY_DEGREE = re.compile(r"\bdegree\b|\bdiploma\b", re.I)

# Equivalent experience accepted instead. "Or related experience" right after
# a degree is the same offer in other words.
_EQUIVALENT = re.compile(
    r"\bor (?:an? )?equivalent\b|\bequivalent (?:practical |work |professional |relevant |"
    r"industry |military )?(?:experience|combination)|\bin lieu of\b|"
    r"\bor (?:relevant|related|comparable|commensurate) (?:work |professional |industry )?"
    r"experience\b|\bor \d+\+? years? of (?:relevant |related |professional )?experience\b|"
    r"\bequivalent (?:education|training)\b", re.I)
# Wanted, not required.
_PREFERRED = re.compile(
    r"\bpreferred\b|\bpreferably\b|\bnice to have\b|\ba plus\b|\bbonus\b|\bideally\b|"
    r"\bdesired\b|\bdesirable\b|\bis an asset\b|\bhighly valued\b", re.I)
# Said outright: no degree needed.
_NO_DEGREE = re.compile(
    r"\bno (?:college |university |4-year |four-year )?degree (?:is )?(?:required|needed|necessary)\b|"
    r"\bdegree (?:is )?not (?:required|needed|necessary)\b|"
    r"\b(?:do not|don't|doesn't|does not) (?:need|require) (?:a |an )?(?:college |university )?degree\b|"
    r"\bwithout a (?:college |university )?degree\b|\bregardless of (?:a )?degree\b", re.I)
# A sentence about the employer's people, not the candidate: "our engineers
# hold PhDs". Skipped unless it also talks to the candidate.
_ABOUT_THEM = re.compile(
    r"\b(?:our|their)\s+(?:\w+\s+){0,3}(?:team|engineers|founders|scientists|researchers|"
    r"staff|employees|people|colleagues)\b|\b(?:we|team members) (?:hold|have)\b", re.I)
_ABOUT_YOU = re.compile(r"\b(?:you|your|candidate|applicant|required|requires|must|"
                        r"minimum|qualification)", re.I)

# Section headings that set the mode of the lines under them.
_HEAD_PREFERRED = re.compile(r"\b(?:preferred|nice to have|bonus|plus(?:es)?|desired|"
                             r"ideal(?:ly)?|extra credit|stand out)\b", re.I)
_HEAD_REQUIRED = re.compile(r"\b(?:required|requirements|minimum|basic|must[- ]have|"
                            r"qualifications|what you(?:'ll)? (?:need|bring)|you have|"
                            r"about you)\b", re.I)


@dataclass
class DegreeFacts:
    level: str = "none"
    equivalent_ok: bool = False
    masters: str | None = None           # "required" | "preferred" | None
    certifications: list[str] = field(default_factory=list)
    evidence: str = ""


@functools.lru_cache(maxsize=1)
def _certifications() -> tuple[tuple[str, re.Pattern], ...]:
    import yaml

    from .config import RESOURCES
    data = yaml.safe_load((RESOURCES / "degree.yaml").read_text(encoding="utf-8")) or {}
    out = []
    for cert in data.get("certifications") or []:
        joined = "|".join(f"(?:{p})" for p in cert.get("patterns") or [])
        if joined:
            out.append((str(cert["name"]), re.compile(joined, re.I)))
    return tuple(out)


def certifications(text: str) -> list[str]:
    return [name for name, pattern in _certifications() if pattern.search(text or "")]


def _pieces(text: str) -> list[tuple[str, str]]:
    """(sentence, mode) in order; mode is 'required', 'preferred' or ''
    from the nearest heading above it."""
    out, mode = [], ""
    for line in (text or "").splitlines():
        line = line.strip(" \t-•*·")
        if not line:
            continue
        words = len(line.split())
        heading = words <= 8 and (line.endswith(":") or not re.search(r"[.;]$", line)) \
            and not _BACHELOR.search(line) and not _GRADUATE.search(line)
        if heading and _HEAD_PREFERRED.search(line):
            mode = "preferred"
            continue
        if heading and _HEAD_REQUIRED.search(line):
            mode = "required"
            continue
        for sentence in re.split(r"(?<=[.!?;])\s+", line):
            if sentence.strip():
                out.append((sentence.strip(), mode))
    return out


def _clip(sentence: str) -> str:
    sentence = " ".join(sentence.split())
    return sentence if len(sentence) <= MAX_EVIDENCE else sentence[:MAX_EVIDENCE - 1] + "…"


def classify(description: str | None) -> DegreeFacts:
    facts = DegreeFacts(certifications=certifications(description or ""))
    bachelor_req = bachelor_soft = graduate_req = graduate_pref = associate = False
    evidence = {}
    for sentence, mode in _pieces(description or ""):
        if _NO_DEGREE.search(sentence):
            evidence.setdefault("no_degree", sentence)
            continue
        has_graduate = bool(_GRADUATE.search(sentence))
        has_associate = bool(_ASSOCIATE.search(sentence))
        # "Master's degree in X" and "Associate's degree in X" are not also a
        # bachelor's because they say "degree in": read the rest only.
        rest = _NAMED_DEGREE_IN.sub(" ", sentence)
        has_bachelor = bool(_BACHELOR.search(rest))
        if not (has_bachelor or has_graduate or has_associate):
            # The alternative often sits in its own clause: "...; OR 2+ years
            # of professional experience in lieu of a degree".
            # ("High school diploma or equivalent" is not one: no college
            # degree is asked for there.)
            if re.search(r"\bdegree\b", sentence, re.I) and _EQUIVALENT.search(sentence) \
                    and not (_ABOUT_THEM.search(sentence) and not _ABOUT_YOU.search(sentence)):
                facts.equivalent_ok = True
                evidence.setdefault("equivalent", sentence)
            continue
        if _ABOUT_THEM.search(sentence) and not _ABOUT_YOU.search(sentence):
            continue
        soft = mode == "preferred" or bool(_PREFERRED.search(sentence))
        equivalent = bool(_EQUIVALENT.search(sentence))
        if equivalent:
            facts.equivalent_ok = True
        if has_graduate:
            # "BS or MS" offers the lower degree: the graduate one is a choice.
            if soft or has_bachelor:
                graduate_pref = True
                evidence.setdefault("graduate_pref", sentence)
            else:
                graduate_req = True
                evidence.setdefault("graduate_req", sentence)
        if has_bachelor:
            if soft or equivalent:
                bachelor_soft = True
                evidence.setdefault("bachelor_soft", sentence)
            else:
                bachelor_req = True
                evidence.setdefault("bachelor_req", sentence)
        elif has_associate and not has_graduate:
            associate = True
            evidence.setdefault("associate", sentence)

    # One stated alternative anywhere ("or equivalent experience") opens the
    # requirement: postings often list the degree and the alternative on
    # separate lines.
    if facts.equivalent_ok:
        bachelor_soft = bachelor_soft or bachelor_req or graduate_req
        bachelor_req = graduate_req = False
    if graduate_pref or graduate_req:
        facts.masters = "required" if graduate_req else "preferred"
    if graduate_req:
        facts.level, key = "masters", "graduate_req"
    elif bachelor_req and graduate_pref:
        facts.level, key = "masters_preferred", "graduate_pref"
    elif bachelor_req:
        facts.level, key = "bachelors", "bachelor_req"
    elif bachelor_soft or graduate_pref:
        # Asked for, but not required: open to people without one.
        facts.level = "bachelors_or_equiv"
        key = "bachelor_soft" if bachelor_soft else "graduate_pref"
    elif associate:
        facts.level, key = "associate", "associate"
    else:
        facts.level, key = "none", "no_degree"
    facts.evidence = _clip(evidence.get(key, ""))
    return facts


def columns(description: str | None) -> dict[str, object]:
    """The jobs-table columns for a description."""
    import json
    facts = classify(description)
    return {"degree_level": facts.level,
            "certs_named": json.dumps(facts.certifications),
            "degree_evidence": facts.evidence or None}


def requires_degree(level: str | None) -> bool | None:
    """A bachelor's or more, required, the question `degree_required` answers."""
    if level is None:
        return None
    return level in ("bachelors", "masters", "masters_preferred")
