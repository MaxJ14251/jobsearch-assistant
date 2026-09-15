"""Resume and cover-letter tailoring.

The engine selects bullets from the master profile and rewords them against a
job description. It never writes new claims — and that distinction is enforced
here rather than requested in a prompt, because the models available on this
tier will not honour it reliably.

Three guarantees, each with a function that fails closed:

* `scrub_prompt`  — identity values never reach the API.
* `verify_draft`  — every output bullet traces to a profile bullet id, and its
                    wording stays close enough to the source that it makes no
                    new factual claim.
* do-not-claim    — nothing from `ats_keywords.aspirational_do_not_claim`
                    appears in generated text.

Why the paranoia is warranted: asked to tailor against a posting demanding
Kubernetes, a model that wants to be helpful writes a Kubernetes bullet. It
reads well, it fits the job, and it is false. The verifier is the only thing
standing between that and a document with the candidate's name on it.
See `tests/test_tailor.py::TestFabricationResistance`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import llm

# A reworded bullet must retain this share of its source's meaningful words.
# Below it, the text has drifted far enough to be asserting something new.
MIN_SOURCE_OVERLAP = 0.45

# Words carried by every bullet; they say nothing about whether meaning survived.
_STOP = {
    "a", "an", "and", "the", "to", "of", "in", "on", "for", "with", "from",
    "by", "at", "as", "into", "that", "this", "it", "its", "is", "are", "was",
    "were", "be", "been", "using", "used", "use", "via", "across", "through",
    "while", "including", "such", "also", "more", "than", "then", "their",
}


class FabricationError(RuntimeError):
    """Generated text made a claim the profile does not support."""


class IdentityLeakError(RuntimeError):
    """An outbound prompt contained personal identifying information."""


@dataclass(frozen=True)
class SourceBullet:
    id: str
    text: str
    tags: tuple[str, ...]
    family: str
    strength: int
    origin: str          # "experience" | "project"
    parent: str          # company or project name


@dataclass
class DraftBullet:
    source_id: str
    text: str


@dataclass
class TailoredDraft:
    job_id: int | None = None
    summary_id: str = ""
    summary: str = ""
    bullets: list[DraftBullet] = field(default_factory=list)
    keywords_matched: list[str] = field(default_factory=list)
    keywords_missing: list[str] = field(default_factory=list)
    model: str = ""


# --- profile access ---------------------------------------------------------


def collect_bullets(profile: dict[str, Any]) -> dict[str, SourceBullet]:
    """Every reusable bullet in the profile, keyed by id."""
    out: dict[str, SourceBullet] = {}
    for exp in profile.get("experience") or []:
        for b in exp.get("bullets") or []:
            out[b["id"]] = SourceBullet(
                id=b["id"], text=" ".join(b["text"].split()),
                tags=tuple(b.get("tags") or []), family=exp.get("family", ""),
                strength=int(b.get("strength", 2)), origin="experience",
                parent=exp.get("company", ""),
            )
    for proj in profile.get("projects") or []:
        for b in proj.get("bullets") or []:
            out[b["id"]] = SourceBullet(
                id=b["id"], text=" ".join(b["text"].split()),
                tags=tuple(b.get("tags") or []), family=proj.get("family", ""),
                strength=int(b.get("strength", 2)), origin="project",
                parent=proj.get("name", ""),
            )
    return out


def do_not_claim(profile: dict[str, Any]) -> list[str]:
    return list(
        (profile.get("ats_keywords") or {}).get("aspirational_do_not_claim") or []
    )


def identity_values(profile: dict[str, Any]) -> list[str]:
    """Strings that must never appear in an outbound prompt."""
    ident = profile.get("identity") or {}
    loc = ident.get("location") or {}
    links = profile.get("links") or {}
    candidates = [
        ident.get("full_name"), ident.get("preferred_name"),
        ident.get("email"), ident.get("phone"),
        loc.get("street"), loc.get("postal_code"),
        links.get("linkedin"), links.get("github"),
        links.get("portfolio"), links.get("website"),
    ]
    out = []
    for value in candidates:
        if not isinstance(value, str):
            continue
        value = value.strip()
        # A first name alone is too short to match on safely; a full name is not.
        if len(value) >= 5:
            out.append(value)
    return out


# --- guarantee 1: identity never leaves the machine -------------------------


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def scrub_prompt(prompt: str, profile: dict[str, Any]) -> str:
    """Return `prompt` unchanged, or raise if it carries identity.

    Deliberately raises rather than silently redacting: a leak means the
    caller built the prompt wrong, and quietly patching it would hide the bug
    while the next caller reintroduces it.
    """
    haystack = prompt.lower()
    for value in identity_values(profile):
        if value.lower() in haystack:
            raise IdentityLeakError(
                f"prompt contains identity value {value[:18]!r}; "
                "personal details are merged into the document locally, "
                "never sent to the API"
            )
    # Phone numbers survive reformatting, so compare digits too.
    phone = (profile.get("identity") or {}).get("phone") or ""
    phone_digits = _digits(phone)
    if len(phone_digits) >= 10 and phone_digits[-10:] in _digits(prompt):
        raise IdentityLeakError("prompt contains the phone number")
    return prompt


# --- guarantee 2: every claim traces to the profile -------------------------


def _words(text: str) -> set[str]:
    return {
        w for w in re.findall(r"[a-z0-9+#.]+", (text or "").lower())
        if w not in _STOP and len(w) > 2
    }


def term_pattern(term: str) -> re.Pattern[str]:
    r"""A boundary-safe matcher for a technology name.

    `\bC\+\+\b` never matches: a word boundary cannot sit between '+' and a
    space, so the trailing `\b` fails on "C++ and Java". The same bug hides
    C#, F# and .NET. Lookarounds that treat +, # and . as part of the token
    handle all of them.
    """
    return re.compile(
        rf"(?<![\w+#.]){re.escape(term)}(?![\w+#])", re.IGNORECASE
    )


def source_overlap(reworded: str, source: str) -> float:
    """Share of the source's meaningful words still present after rewording."""
    src = _words(source)
    if not src:
        return 0.0
    return len(src & _words(reworded)) / len(src)


def verify_draft(draft: TailoredDraft, profile: dict[str, Any]) -> TailoredDraft:
    """Raise FabricationError unless every bullet is traceable and faithful."""
    sources = collect_bullets(profile)
    banned = [t for t in do_not_claim(profile) if t]

    for bullet in draft.bullets:
        source = sources.get(bullet.source_id)
        if source is None:
            raise FabricationError(
                f"bullet cites unknown source id {bullet.source_id!r} — "
                "generated text must map to a master_profile.yaml bullet"
            )
        overlap = source_overlap(bullet.text, source.text)
        if overlap < MIN_SOURCE_OVERLAP:
            raise FabricationError(
                f"bullet {bullet.source_id!r} drifted from its source "
                f"({overlap:.0%} word overlap, minimum {MIN_SOURCE_OVERLAP:.0%}): "
                f"{bullet.text[:90]!r}"
            )

    generated = " ".join([draft.summary] + [b.text for b in draft.bullets]).lower()
    for term in banned:
        if term_pattern(term).search(generated):
            raise FabricationError(
                f"generated text claims {term!r}, which is on the "
                "aspirational_do_not_claim list"
            )
    return draft


# --- keyword gap ------------------------------------------------------------


def keyword_gap(
    description: str, profile: dict[str, Any]
) -> tuple[list[str], list[str]]:
    """(matched, missing) — what the posting wants versus what can be claimed.

    `missing` is the honest gap report. It must never be trimmed to flatter the
    document; it is what tells the candidate where they actually stand.
    """
    blob = (description or "").lower()
    have = [k for k in ((profile.get("ats_keywords") or {}).get("have") or [])]
    matched = [k for k in have if term_pattern(k).search(blob)]
    missing = [t for t in do_not_claim(profile) if term_pattern(t).search(blob)]
    return matched, missing


# --- selection --------------------------------------------------------------


def pick_summary(profile: dict[str, Any], track: str, description: str) -> dict:
    """Choose the summary variant that fits the role."""
    summaries = {s["family"]: s for s in profile.get("summaries") or []}
    blob = (description or "").lower()
    if track == "sales" or re.search(
        r"customer[- ]facing|client[- ]facing|account executive|sales", blob
    ):
        return summaries.get("customer_facing_technical") or summaries["general"]
    if re.search(r"\bllm\b|generative ai|agentic|prompt", blob):
        return summaries.get("ai_engineering") or summaries["general"]
    return summaries.get("general") or next(iter(summaries.values()))


def select_bullets(
    profile: dict[str, Any], description: str, track: str, limit: int = 6
) -> list[SourceBullet]:
    """Rank profile bullets against the posting. Deterministic, no model call."""
    blob = (description or "").lower()
    preferred = (
        {"sales", "technical_field"} if track == "sales"
        else {"ai_engineering", "data_ml"}
    )
    scored: list[tuple[float, SourceBullet]] = []
    for bullet in collect_bullets(profile).values():
        hits = sum(
            1 for tag in bullet.tags
            if re.search(rf"\b{re.escape(tag.replace('-', ' ').lower())}", blob)
        )
        score = hits * 2.0
        score += (4 - bullet.strength)              # strength 1 outranks 3
        score += 2.0 if bullet.family in preferred else 0.0
        scored.append((score, bullet))
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return [b for _, b in scored[:limit]]


# --- generation -------------------------------------------------------------

SYSTEM = (
    "You rewrite resume bullets. You may rephrase for emphasis and fit, but you "
    "may NEVER introduce a skill, technology, employer, metric or achievement "
    "that is not already present in the bullet you were given. If the job asks "
    "for something the bullets do not contain, leave it out entirely."
)

PROMPT = """Rewrite each bullet below so it reads naturally for this role.

RULES
- Keep every factual claim identical. Rephrasing only.
- Do NOT add technologies, tools, metrics or responsibilities that are absent
  from the original bullet, even if the job asks for them.
- Return JSON: {{"summary": "...", "bullets": [{{"id": "<same id>", "text": "..."}}]}}

ROLE: {title}
WHAT THE ROLE INVOLVES:
{description}

SUMMARY TO ADAPT:
{summary}

BULLETS (keep each id):
{bullets}"""


def tailor(
    job: dict[str, Any],
    profile: dict[str, Any],
    *,
    models: list[str] | None = None,
) -> TailoredDraft:
    """Produce a verified draft. Raises rather than returning unsafe output."""
    description = job.get("description") or ""
    track = job.get("track") or "engineering"
    summary = pick_summary(profile, track, description)
    chosen = select_bullets(profile, description, track)
    matched, missing = keyword_gap(description, profile)

    listing = "\n".join(f'- [{b.id}] {b.text}' for b in chosen)
    prompt = PROMPT.format(
        title=job.get("title") or "the role",
        description=description[:4000],
        summary=" ".join(summary["text"].split()),
        bullets=listing,
    )
    scrub_prompt(prompt, profile)          # fails closed before any network call

    data, usage = llm.complete_json(
        prompt, system=SYSTEM, models=models, max_tokens=1200,
        temperature=0.3, thinking=False, attempts=2,
    )

    by_id = {b.id: b for b in chosen}
    bullets = []
    for item in (data.get("bullets") or []) if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        bid, text = item.get("id"), item.get("text")
        if isinstance(bid, str) and isinstance(text, str) and text.strip():
            bullets.append(DraftBullet(source_id=bid, text=" ".join(text.split())))
    # Anything the model dropped falls back to its source text verbatim, which
    # is always safe — the original claim, unchanged.
    seen = {b.source_id for b in bullets}
    for b in chosen:
        if b.id not in seen:
            bullets.append(DraftBullet(source_id=b.id, text=b.text))

    draft = TailoredDraft(
        job_id=job.get("id"),
        summary_id=summary["id"],
        summary=" ".join(str(data.get("summary") or summary["text"]).split())
        if isinstance(data, dict) else summary["text"],
        bullets=bullets,
        keywords_matched=matched,
        keywords_missing=missing,
        model=usage.model,
    )
    return verify_draft(draft, profile)
