"""The resume report: what to add to the profile, never what to claim.

ADR 0023. Local and deterministic: pure functions over the profile and,
when there is one, the draft. No model is called and nothing here imports
`jsa.llm` (tests/test_coach.py asserts it). Findings never block a draft or
an approval; `jsa coach --strict` exits non-zero for anyone who wants a gate
in their own workflow.

Each finding names a rule id, says what is missing and where in the profile
to add it. The messages say "if you have one" / "only if true": the tool
may never write a claim, so it can only point at the gap.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import asdict, dataclass
from typing import Any

from .config import load_coach

RULES = ("thin_entry", "no_numbers", "unexplained_gap", "in_dev_only",
         "soft_skill", "vague_summary", "opaque_cert", "missing_skill_evidence")

# unexplained_gap: months without a job, a dated project or a certification
# before the report mentions it. Not measured: the plan's default, and a
# common reading of what a reviewer asks about. `coach.gap_months` overrides.
GAP_MONTHS = 6
# no_numbers fires when more than this share of an entry's bullets have no
# figure. Not measured either; a share rather than "any", because some fields
# rarely use numbers and one unquantified bullet is normal.
NO_NUMBERS_SHARE = 0.5


@dataclass
class Finding:
    id: str
    message: str
    where: str

    def line(self) -> str:
        return f"{self.id}: {self.message} ({self.where})"

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


# --- dates -------------------------------------------------------------------


def _month(value: Any, *, end: bool = False) -> int | None:
    """A month index (year * 12 + month - 1). A bare year means January as a
    start and December as an end."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.date):
        return value.year * 12 + value.month - 1
    m = re.fullmatch(r"(\d{4})(?:-(\d{1,2}))?(?:-\d{1,2})?", str(value).strip())
    if not m:
        return None
    month = int(m[2]) if m[2] else (12 if end else 1)
    return int(m[1]) * 12 + month - 1


def _label(index: int) -> str:
    from .render import MONTHS
    return f"{MONTHS[index % 12]} {index // 12}"


# --- the rules -----------------------------------------------------------------


def _shown_experience(profile: dict[str, Any], draft) -> list[tuple[int, dict, list[str]]]:
    """(index, entry, bullet texts shown): the draft's, or the whole profile's."""
    from .tailor import collect_bullets, entry_key

    entries = list(enumerate(profile.get("experience") or []))
    if draft is None:
        return [(i, e, [str(b.get("text", "")) for b in e.get("bullets") or []])
                for i, e in entries]
    sources = collect_bullets(profile)
    shown: dict[str, list[str]] = {}
    for b in draft.bullets:
        src = sources.get(b.source_id)
        if src and src.origin == "experience":
            shown.setdefault(src.parent, []).append(b.text)
    return [(i, e, shown[entry_key(e, "company", "title")]) for i, e in entries
            if entry_key(e, "company", "title") in shown]


def thin_entry(profile, draft, cfg) -> list[Finding]:
    out = []
    for i, entry, texts in _shown_experience(profile, draft):
        if len(texts) < 2:
            out.append(Finding(
                "thin_entry",
                f'"{entry.get("title")}" at {entry.get("company")} shows '
                f"{len(texts)} bullet{'s' if len(texts) != 1 else ''}. Add another "
                "if you have one: what you owned, built or improved there",
                f"experience[{i}].bullets"))
    return out


def no_numbers(profile, draft, cfg) -> list[Finding]:
    out = []
    for i, entry, texts in _shown_experience(profile, draft):
        bare = [t for t in texts if not re.search(r"\d", t)]
        if texts and len(bare) / len(texts) > NO_NUMBERS_SHARE:
            out.append(Finding(
                "no_numbers",
                f'{len(bare)} of {len(texts)} bullets under "{entry.get("title")}" '
                "have no figure. Add one if you have it (how many, how often, "
                "how much, how fast)",
                f"experience[{i}].bullets"))
    return out


def unexplained_gap(profile, draft, cfg, today: dt.date) -> list[Finding]:
    now = today.year * 12 + today.month - 1
    gap_months = int((profile.get("coach") or {}).get("gap_months") or GAP_MONTHS)
    jobs = []
    for e in profile.get("experience") or []:
        start = _month(e.get("start"))
        if start is None:
            continue
        end = now if e.get("current") else _month(e.get("end"), end=True)
        jobs.append((start, end if end is not None else start))
    if not jobs:
        return []
    jobs.sort()
    merged = [list(jobs[0])]
    for start, end in jobs[1:]:
        if start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    gaps = [(a[1] + 1, b[0] - 1) for a, b in zip(merged, merged[1:])]
    if merged[-1][1] < now:
        gaps.append((merged[-1][1] + 1, now))

    covers = []
    for p in profile.get("projects") or []:
        start = _month(p.get("start"))
        if start is not None:
            end = _month(p.get("end"), end=True)
            covers.append((start, end if end is not None else now))
    for c in profile.get("certifications") or []:
        issued = _month(c.get("issued"))
        if issued is not None:
            covers.append((issued, issued))

    out = []
    for a, b in gaps:
        if b - a + 1 <= gap_months:
            continue
        if any(s <= b and e >= a for s, e in covers):
            continue
        until = "now" if b == now else _label(b)
        out.append(Finding(
            "unexplained_gap",
            f"{b - a + 1} months, {_label(a)} to {until}, have no job, dated "
            "project or certification. If you did independent work then, add "
            "it as a project with start: and end: (only if true); otherwise "
            "have a one-line answer ready",
            "experience / projects"))
    return out


def in_dev_only(profile, draft, cfg) -> list[Finding]:
    leads = tuple(str(x).lower() for x in cfg.get("in_progress_leads") or ["goal:"])
    out = []
    for i, p in enumerate(profile.get("projects") or []):
        texts = [str(b.get("text", "")).strip() for b in p.get("bullets") or []]
        if not texts:
            continue
        def ongoing(t: str) -> bool:
            first = (t.split() or [""])[0]
            return t.lower().startswith(leads) or first.lower().endswith("ing")
        if all(ongoing(t) for t in texts):
            out.append(Finding(
                "in_dev_only",
                f'every bullet under "{p.get("name")}" describes work in '
                "progress. If any part works today, say what it does in its own "
                "bullet (only if true)",
                f"projects[{i}].bullets"))
    return out


def soft_skill(profile, draft, cfg) -> list[Finding]:
    traits = [str(t).lower() for t in cfg.get("soft_skills") or []]
    out = []
    for key, values in (profile.get("skills") or {}).items():
        if key == "unverified_candidates" or not isinstance(values, list):
            continue
        for value in values:
            low = str(value).lower()
            if any(re.search(r"(?<![\w-])" + re.escape(t) + r"(?![\w-])", low)
                   for t in traits):
                out.append(Finding(
                    "soft_skill",
                    f'"{value}" is a trait a reader can\'t check. Show it in a '
                    "bullet instead, and keep skills to tools and methods",
                    f"skills.{key}"))
    return out


def vague_summary(profile, draft, cfg) -> list[Finding]:
    phrases = [str(p).lower() for p in cfg.get("filler_phrases") or []]
    if draft is not None:
        texts = [(draft.summary_id or "summary", draft.summary)] if draft.summary else []
    else:
        texts = [(s.get("id", f"summaries[{n}]"), str(s.get("text", "")))
                 for n, s in enumerate(profile.get("summaries") or [])]
    out = []
    for sid, text in texts:
        hits = [p for p in phrases if p in text.lower()]
        if hits:
            out.append(Finding(
                "vague_summary",
                f"the summary says {', '.join(repr(h) for h in hits)}. Replace "
                "it with something you did",
                f"summaries[{sid}]"))
    return out


_CODE = re.compile(r"^(?:[A-Z0-9]+(?:[-_./][A-Z0-9]+)+|[A-Z]+\d[A-Z0-9]*)$")


def opaque_cert(profile, draft, cfg) -> list[Finding]:
    acronyms = {str(a).upper() for a in cfg.get("acronyms") or []}
    out = []
    for i, c in enumerate(profile.get("certifications") or []):
        if c.get("display_name") or c.get("description"):
            continue
        codes = [t for t in re.split(r"[\s,()]+", str(c.get("name") or ""))
                 if _CODE.match(t) and t.upper() not in acronyms]
        if codes:
            out.append(Finding(
                "opaque_cert",
                f'"{c.get("name")}" reads as a code ({", ".join(codes)}). Add '
                "display_name: with what a reader would recognise, or a "
                "description:",
                f"certifications[{i}]"))
    return out


def missing_skill_evidence(profile, draft, cfg) -> list[Finding]:
    """A project's stack, or an ats_keywords.have term your bullets use, that
    your skills don't list. Narrowed from "any capitalized word in a bullet",
    which would fire on employers, places and sentence starts."""
    from .tailor import collect_bullets, term_pattern

    listed = {str(v).lower() for vals in (profile.get("skills") or {}).values()
              if isinstance(vals, list) for v in vals}
    out = []
    for i, p in enumerate(profile.get("projects") or []):
        missing = [s for s in p.get("stack") or [] if str(s).lower() not in listed]
        if missing:
            out.append(Finding(
                "missing_skill_evidence",
                f'"{p.get("name")}" uses {", ".join(map(str, missing))}, which '
                "your skills don't list. Add the ones you can talk about",
                f"projects[{i}].stack -> skills"))
    text = " ".join(b.text for b in collect_bullets(profile).values()).lower()
    have = [str(k) for k in (profile.get("ats_keywords") or {}).get("have") or []]
    used = [k for k in have if k.lower() not in listed and term_pattern(k).search(text)]
    if used:
        out.append(Finding(
            "missing_skill_evidence",
            f"your bullets show {', '.join(used)}, which your skills don't "
            "list. Add the ones you can talk about",
            "ats_keywords.have -> skills"))
    return out


def review(profile: dict[str, Any], draft=None, *,
           today: dt.date | None = None,
           config: dict[str, Any] | None = None) -> list[Finding]:
    """Every enabled rule's findings, in RULES order."""
    cfg = load_coach() if config is None else config
    off = set((profile.get("coach") or {}).get("disabled") or [])
    today = today or dt.date.today()
    found: list[Finding] = []
    for rule in RULES:
        if rule in off:
            continue
        fn = globals()[rule]
        found += (fn(profile, draft, cfg, today) if rule == "unexplained_gap"
                  else fn(profile, draft, cfg))
    return found
